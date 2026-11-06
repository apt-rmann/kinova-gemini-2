// Kortex protection zones: list | clear <backup> | restore <backup>. Build:
//   K=src/kortex_controller/kortex_api; g++ -std=c++17 -D_OS_UNIX -I$K/include -I$K/include/client -I$K/include/client_stubs -I$K/include/common -I$K/include/messages scratch/protection_zones.cpp $K/lib/release/libKortexApiCpp.a -lpthread -o scratch/protection_zones
#include <BaseClientRpc.h>
#include <SessionManager.h>
#include <RouterClient.h>
#include <TransportClientTcp.h>
#include <fstream>
#include <iostream>
#include <string>
namespace k_api = Kinova::Api;
int main(int argc, char** argv) {
    std::string mode = argc > 1 ? argv[1] : "list";
    auto t = new k_api::TransportClientTcp();
    auto r = new k_api::RouterClient(t, [](k_api::KError e){ std::cerr << e.toString() << "\n"; });
    t->connect("192.168.77.10", 10000);
    auto sm = new k_api::SessionManager(r);
    auto info = k_api::Session::CreateSessionInfo();
    info.set_username("admin"); info.set_password("admin");
    info.set_session_inactivity_timeout(60000); info.set_connection_inactivity_timeout(2000);
    sm->CreateSession(info);
    auto base = new k_api::Base::BaseClient(r);
    int rc = 0;
    try {
        if (mode == "clear") {
            auto zones = base->ReadAllProtectionZones();
            std::ofstream f(argv[2], std::ios::binary);
            if (!zones.SerializeToOstream(&f)) throw std::runtime_error("backup write failed");
            f.close();
            std::cout << "backed up " << zones.protection_zones_size() << " zone(s) to " << argv[2] << "\n";
            for (auto& z : zones.protection_zones()) {
                try {
                    base->DeleteProtectionZone(z.handle());
                    std::cout << "deleted '" << z.name() << "'\n";
                } catch (k_api::KDetailedException& ex) {   // the factory base zone is read-only
                    std::cout << "kept '" << z.name() << "' (" << ex.what() << ")\n";
                }
            }
        } else if (mode == "restore") {
            k_api::Base::ProtectionZoneList zones;
            std::ifstream f(argv[2], std::ios::binary);
            if (!zones.ParseFromIstream(&f)) throw std::runtime_error("backup read failed");
            auto existing = base->ReadAllProtectionZones();
            for (auto z : zones.protection_zones()) {
                bool present = false;
                for (auto& e : existing.protection_zones()) present |= (e.name() == z.name());
                if (present) { std::cout << "'" << z.name() << "' already present\n"; continue; }
                z.clear_handle();
                base->CreateProtectionZone(z);
                std::cout << "restored '" << z.name() << "'\n";
            }
        }
        auto zones = base->ReadAllProtectionZones();
        std::cout << zones.protection_zones_size() << " zone(s) on the arm now\n";
        for (auto& z : zones.protection_zones())
            std::cout << "- '" << z.name() << "' enabled=" << z.is_enabled() << "\n";
    } catch (k_api::KDetailedException& ex) {
        std::cerr << "Kortex error: " << ex.what() << "\n"; rc = 1;
    } catch (std::exception& ex) {
        std::cerr << "error: " << ex.what() << "\n"; rc = 1;
    }
    sm->CloseSession(); r->SetActivationStatus(false); t->disconnect();
    return rc;
}
