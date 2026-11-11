#include "kortex_controller/controller.hpp"
#include <rclcpp_action/rclcpp_action.hpp>
#include "ros2_interfaces/action/move_to_pose.hpp"
#include "ros2_interfaces/action/move_to_joints.hpp"
#include "ros2_interfaces/action/command_gripper.hpp"

using namespace std::placeholders;


Controller::Controller() : Node("kortex_controller")
{
    // Parameters
    this->declare_parameter("robot_ip", "192.168.77.10");
    this->declare_parameter("username", "admin");
    this->declare_parameter("password", "admin");
    this->declare_parameter("config_path", std::string(std::getenv("HOME") ? std::getenv("HOME") : "") + "/kinova-gemini/config.yaml");

    this->declare_parameter("ik_attempts", 50);
    this->declare_parameter("ik_position_tolerance_m", 0.005);
    this->declare_parameter("ik_orientation_tolerance_deg", 2.0);

    // z of the bare gripper's TCP in the Kortex tool configuration; written to the arm at startup
    this->declare_parameter("bare_tool_z", 0.22);

    // ServoToTarget: twist = kp * vector + kd * d(vector)/dt, capped at max speed
    this->declare_parameter("servo_kp", 1.0);
    this->declare_parameter("servo_kd", 0.0);
    this->declare_parameter("servo_max_speed", 0.10);
    this->declare_parameter("servo_vector_timeout_s", 0.5);
    this->declare_parameter("servo_timeout_s", 60.0);

    std::string robot_ip = this->get_parameter("robot_ip").as_string();
    mIKAttempts = std::max(1, static_cast<int>(this->get_parameter("ik_attempts").as_int()));
    mIKPositionTol = this->get_parameter("ik_position_tolerance_m").as_double();
    mIKOrientationTol = this->get_parameter("ik_orientation_tolerance_deg").as_double();
    mServoKp = this->get_parameter("servo_kp").as_double();
    mServoKd = this->get_parameter("servo_kd").as_double();
    mServoMaxSpeed = this->get_parameter("servo_max_speed").as_double();
    mServoVectorTimeout = this->get_parameter("servo_vector_timeout_s").as_double();
    mServoTimeout = this->get_parameter("servo_timeout_s").as_double();

    // --- Kortex API Setup ---
    mTransport = new k_api::TransportClientTcp();
    mRouter = new k_api::RouterClient(mTransport, [](k_api::KError err) {
        RCLCPP_ERROR(rclcpp::get_logger("kortex_api"), "Kortex Transport Error: %s", err.toString().c_str());
    });
    mTransport->connect(robot_ip, 10000);

    auto session_manager = new k_api::SessionManager(mRouter);
    auto create_session_info = k_api::Session::CreateSessionInfo();
    create_session_info.set_username(this->get_parameter("username").as_string());
    create_session_info.set_password(this->get_parameter("password").as_string());
    create_session_info.set_session_inactivity_timeout(60000);
    create_session_info.set_connection_inactivity_timeout(2000);
    session_manager->CreateSession(create_session_info);

    mBase = new k_api::Base::BaseClient(mRouter);
    mBaseCyclic = new k_api::BaseCyclic::BaseCyclicClient(mRouter);
    mControlConfig = new k_api::ControlConfig::ControlConfigClient(mRouter);

    // --- Tool TCP ---
    // the tool configuration persists on the arm, so every run starts from the bare gripper.
    // Mass and center of mass are kept from the robot; only the TCP z is ours.
    mBareTool = mControlConfig->GetToolConfiguration();
    mBareTool.mutable_tool_transform()->set_z(static_cast<float>(this->get_parameter("bare_tool_z").as_double()));
    std::string tool_error;
    if (!setToolLength(0.0f, tool_error)) {
        RCLCPP_ERROR(this->get_logger(), "Could not reset the tool TCP at startup: %s", tool_error.c_str());
    }

    // --- Action Servers ---
    this->CommandGripperServer = rclcpp_action::create_server<ros2_interfaces::action::CommandGripper>(
        this, "command_gripper",
        std::bind(&Controller::handle_gripper_goal, this, _1, _2),
        std::bind(&Controller::handle_gripper_cancel, this, _1),
        std::bind(&Controller::handle_gripper_accepted, this, _1));

    this->MoveStraightServer = rclcpp_action::create_server<ros2_interfaces::action::MoveStraight>(
        this, "move_straight",
        std::bind(&Controller::handle_straight_goal, this, _1, _2),
        std::bind(&Controller::handle_straight_cancel, this, _1),
        std::bind(&Controller::handle_straight_accepted, this, _1));

    this->MoveToJointsServer = rclcpp_action::create_server<ros2_interfaces::action::MoveToJoints>(
        this, "move_to_joints",
        std::bind(&Controller::handle_joints_goal, this, _1, _2),
        std::bind(&Controller::handle_joints_cancel, this, _1),
        std::bind(&Controller::handle_joints_accepted, this, _1));

    this->MoveToPoseServer = rclcpp_action::create_server<ros2_interfaces::action::MoveToPose>(
        this, "move_to_pose",
        std::bind(&Controller::handle_pose_goal, this, _1, _2),
        std::bind(&Controller::handle_pose_cancel, this, _1),
        std::bind(&Controller::handle_pose_accepted, this, _1));

    this->ServoToTargetServer = rclcpp_action::create_server<ros2_interfaces::action::ServoToTarget>(
        this, "servo_to_target",
        std::bind(&Controller::handle_servo_goal, this, _1, _2),
        std::bind(&Controller::handle_servo_cancel, this, _1),
        std::bind(&Controller::handle_servo_accepted, this, _1));

    // --- Service Servers ---
    this->ComputeIKService = this->create_service<ros2_interfaces::srv::ComputeIK>(
        "compute_ik", std::bind(&Controller::handleComputeIK, this, _1, _2));
    this->SetToolLengthService = this->create_service<ros2_interfaces::srv::SetToolLength>(
        "set_tool_length", std::bind(&Controller::handleSetToolLength, this, _1, _2));

    // --- Publishers ---
    PubState = this->create_publisher<ros2_interfaces::msg::RobotState>("robot_state", 10);
    // robot_state_publisher needs this to build the TF chain through the arm's revolute joints
    PubJointState = this->create_publisher<sensor_msgs::msg::JointState>("/joint_states", 10);
    Timer = this->create_wall_timer(std::chrono::milliseconds(50), std::bind(&Controller::publishState, this));

    // --- Subscribers ---
    SubStop = this->create_subscription<std_msgs::msg::Empty>(
        "/stop", 10, std::bind(&Controller::handleStop, this, _1));

    // open-loop joint speed command: 7 values in deg/s, all zeros halts the arm
    SubJointVelocity = this->create_subscription<std_msgs::msg::Float64MultiArray>(
        "/joint_velocity", 10, std::bind(&Controller::handleJointVelocity, this, _1));

    // ServoToTarget's target: vector from the TCP to the target, base frame, meters
    SubServoVector = this->create_subscription<geometry_msgs::msg::Vector3Stamped>(
        "/servo_vector", 10, std::bind(&Controller::handleServoVector, this, _1));

    // halt the arm when ROS shuts down (Ctrl-C / SIGINT from ros2 launch): Kortex runs pose and
    // joint moves on the arm itself, so they would otherwise finish after the node is gone
    this->get_node_base_interface()->get_context()->add_pre_shutdown_callback([this]() {
        stopArm();
        RCLCPP_WARN(this->get_logger(), "Controller shutting down - arm halted.");
    });

    RCLCPP_INFO(this->get_logger(), "Kinova Controller Initialized");
    
}

// --- Action Callbacks --- 
// upon accept each action spins up a new thread to execute robot movement

// Command Gripper Callbacks
rclcpp_action::GoalResponse Controller::handle_gripper_goal(const rclcpp_action::GoalUUID &, std::shared_ptr<const CommandGripper::Goal>) {
    mStopRequested = false;
    return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
}
rclcpp_action::CancelResponse Controller::handle_gripper_cancel(const std::shared_ptr<GoalHandleCommandGripper>) {
    return rclcpp_action::CancelResponse::ACCEPT;
}
void Controller::handle_gripper_accepted(const std::shared_ptr<GoalHandleCommandGripper> goal_handle) {
    std::thread{std::bind(&Controller::execute_gripper, this, std::placeholders::_1), goal_handle}.detach();
}

// Move Straight Callbacks
rclcpp_action::GoalResponse Controller::handle_straight_goal(const rclcpp_action::GoalUUID &, std::shared_ptr<const MoveStraight::Goal>) {
    mStopRequested = false;
    return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
}
rclcpp_action::CancelResponse Controller::handle_straight_cancel(const std::shared_ptr<GoalHandleMoveStraight>) {
    return rclcpp_action::CancelResponse::ACCEPT;
}
void Controller::handle_straight_accepted(const std::shared_ptr<GoalHandleMoveStraight> goal_handle) {
    std::thread{std::bind(&Controller::execute_straight, this, std::placeholders::_1), goal_handle}.detach();
}

// Move to Joints Callbacks
rclcpp_action::GoalResponse Controller::handle_joints_goal(const rclcpp_action::GoalUUID &, std::shared_ptr<const ros2_interfaces::action::MoveToJoints::Goal>) {
    mStopRequested = false;
    return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
}
rclcpp_action::CancelResponse Controller::handle_joints_cancel(const std::shared_ptr<GoalHandleMoveToJoints>) {
    return rclcpp_action::CancelResponse::ACCEPT;
}
void Controller::handle_joints_accepted(const std::shared_ptr<GoalHandleMoveToJoints> goal_handle) {
    std::thread{std::bind(&Controller::execute_joints, this, std::placeholders::_1), goal_handle}.detach();
}

// Move to Pose Callbacks
rclcpp_action::GoalResponse Controller::handle_pose_goal(const rclcpp_action::GoalUUID &, std::shared_ptr<const ros2_interfaces::action::MoveToPose::Goal>) {
    mStopRequested = false;
    return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
}
rclcpp_action::CancelResponse Controller::handle_pose_cancel(const std::shared_ptr<GoalHandleMoveToPose>) {
    return rclcpp_action::CancelResponse::ACCEPT;
}
void Controller::handle_pose_accepted(const std::shared_ptr<GoalHandleMoveToPose> goal_handle) {
    std::thread{std::bind(&Controller::execute_pose, this, std::placeholders::_1), goal_handle}.detach();
}

// Servo to Target Callbacks
rclcpp_action::GoalResponse Controller::handle_servo_goal(const rclcpp_action::GoalUUID &, std::shared_ptr<const ServoToTarget::Goal>) {
    mStopRequested = false;
    return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
}
rclcpp_action::CancelResponse Controller::handle_servo_cancel(const std::shared_ptr<GoalHandleServoToTarget>) {
    return rclcpp_action::CancelResponse::ACCEPT;
}
void Controller::handle_servo_accepted(const std::shared_ptr<GoalHandleServoToTarget> goal_handle) {
    std::thread{std::bind(&Controller::execute_servo, this, std::placeholders::_1), goal_handle}.detach();
}


// --- Publishers ---
void Controller::publishState()
{
    std::lock_guard<std::mutex> lock(mApiMutex);
    try {
        auto cartesian = mBase->GetMeasuredCartesianPose();
        auto joints = mBase->GetMeasuredJointAngles();
        float gripper_pos = get_gripper_position();

        auto msg = ros2_interfaces::msg::RobotState();
        msg.x = cartesian.x(); msg.y = cartesian.y(); msg.z = cartesian.z();
        msg.theta_x = cartesian.theta_x(); msg.theta_y = cartesian.theta_y(); msg.theta_z = cartesian.theta_z();
        for (int i = 0; i < 7; ++i) msg.joint_angles[i] = joints.joint_angles(i).value();
        msg.gripper_position = gripper_pos;
        msg.tool_length = mToolLength;
        PubState->publish(msg);

        publishJointStates(joints, gripper_pos);
    } catch (...) {}
}


void Controller::publishJointStates(const k_api::Base::JointAngles& joints, float gripper_pos)
{
    // Kortex reports degrees in [0, 360); the URDF wants radians in (-pi, pi]
    auto to_radians = [](float degrees) {
        double rad = std::fmod(degrees * M_PI / 180.0 + M_PI, 2.0 * M_PI);
        if (rad < 0.0) rad += 2.0 * M_PI;
        return rad - M_PI;
    };

    auto msg = sensor_msgs::msg::JointState();
    msg.header.stamp = this->now();

    msg.name.reserve(8);
    msg.position.reserve(8);
    for (int i = 0; i < 7; ++i) {
        msg.name.push_back("joint_" + std::to_string(i + 1));
        msg.position.push_back(to_radians(joints.joint_angles(i).value()));
    }

    // the driving gripper joint; robot_state_publisher derives the five mimic joints from it
    msg.name.push_back("finger_joint");
    msg.position.push_back(std::clamp(static_cast<double>(gripper_pos), 0.0, 1.0) * FINGER_JOINT_CLOSED_RAD);

    PubJointState->publish(msg);
}


// --- Subscribers ---
void Controller::handleStop(const std_msgs::msg::Empty::SharedPtr)
{
    stopArm();
    RCLCPP_WARN(this->get_logger(), "Stop requested on /stop - arm halted.");
}


void Controller::stopArm()
{
    // halt the arm immediately, independent of whatever action thread is running
    {
        std::lock_guard<std::mutex> lock(mApiMutex);
        try {
            mBase->Stop();
        } catch (std::exception& ex) {
            RCLCPP_ERROR(this->get_logger(), "Kortex error while stopping: %s", ex.what());
        }
    }
    // every motion polling loop checks this and gives up
    mStopRequested = true;
}


void Controller::handleJointVelocity(const std_msgs::msg::Float64MultiArray::SharedPtr msg)
{
    if (msg->data.size() != 7) {
        RCLCPP_ERROR(this->get_logger(), "/joint_velocity needs 7 values, got %zu", msg->data.size());
        return;
    }

    // Kortex latches the last speed command, so all-zeros is how the caller stops the arm
    bool all_zero = true;
    for (double v : msg->data) {
        if (v != 0.0) { all_zero = false; break; }
    }

    std::lock_guard<std::mutex> lock(mApiMutex);
    try {
        if (all_zero) {
            mBase->Stop();
            return;
        }

        k_api::Base::JointSpeeds speeds;
        for (int i = 0; i < 7; ++i) {
            auto s = speeds.add_joint_speeds();
            s->set_joint_identifier(i);
            s->set_value(static_cast<float>(msg->data[i]));
        }
        // runs until the next command or a Stop
        mBase->SendJointSpeedsCommand(speeds);
    } catch (std::exception& ex) {
        RCLCPP_ERROR(this->get_logger(), "Kortex error on /joint_velocity: %s", ex.what());
    }
}


void Controller::handleServoVector(const geometry_msgs::msg::Vector3Stamped::SharedPtr msg)
{
    std::lock_guard<std::mutex> lock(mServoMutex);
    mServoVector[0] = msg->vector.x;
    mServoVector[1] = msg->vector.y;
    mServoVector[2] = msg->vector.z;
    mServoStamp = std::chrono::steady_clock::now();
    ++mServoSeq;
}


// ----- Helper Functions ---

template<typename ActionT>
Controller::PollOutcome Controller::pollUntilCartesianTarget(
    const std::shared_ptr<rclcpp_action::ServerGoalHandle<ActionT>> goal_handle, double target_x, double target_y, double target_z, const char* label, double timeout_s, std::string& message_out)
{
    // to check to see when the robot has moved where we wanted it to go
    const auto start = std::chrono::steady_clock::now();

    while (rclcpp::ok()) {
        if (goal_handle->is_canceling()) {
            std::lock_guard<std::mutex> lock(mApiMutex);
            mBase->Stop();
            RCLCPP_WARN(this->get_logger(), "%s cancelled.", label);
            message_out = "canceled";
            return PollOutcome::CANCELLED;
        }

        if (mStopRequested) {
            // the /stop callback already halted the arm, but stop again in case this goal started after it
            std::lock_guard<std::mutex> lock(mApiMutex);
            mBase->Stop();
            RCLCPP_WARN(this->get_logger(), "%s stopped by /stop.", label);
            message_out = "stopped by /stop";
            return PollOutcome::STOPPED;
        }

        k_api::Base::Pose current;
        {
            std::lock_guard<std::mutex> lock(mApiMutex);
            current = mBase->GetMeasuredCartesianPose();
        }

        double dist = sqrt(pow(target_x - current.x(), 2) + pow(target_y - current.y(), 2) + pow(target_z - current.z(), 2));
        if (dist < 0.01) {
            message_out = "reached";
            return PollOutcome::REACHED;
        }

        if (timeout_s > 0.0) {
            double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
            if (elapsed > timeout_s) {
                std::lock_guard<std::mutex> lock(mApiMutex);
                mBase->Stop();
                RCLCPP_ERROR(this->get_logger(), "%s timed out after %.1f s - stopping the arm.", label, elapsed);
                message_out = "timed out after " + std::to_string(static_cast<int>(elapsed)) + " s";
                return PollOutcome::TIMED_OUT;
            }
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    message_out = "node shutting down";
    return PollOutcome::TIMED_OUT;
}


double Controller::angularDelta(double from_deg, double to_deg)
{
    // shortest signed rotation from -> to, in (-180, 180]
    double d = std::fmod(to_deg - from_deg, 360.0);
    if (d > 180.0)  d -= 360.0;
    if (d < -180.0) d += 360.0;
    return d;
}


double Controller::jointMotionCost(const k_api::Base::JointAngles& from, const k_api::Base::JointAngles& to)
{
    // total joint travel in degrees, taking the short way around for continuous joints
    double cost = 0.0;
    for (int i = 0; i < from.joint_angles_size() && i < to.joint_angles_size(); ++i) {
        cost += std::fabs(angularDelta(from.joint_angles(i).value(), to.joint_angles(i).value()));
    }
    return cost;
}


bool Controller::poseMatches(const k_api::Base::Pose& a, const k_api::Base::Pose& b) const
{
    const double dx = a.x() - b.x();
    const double dy = a.y() - b.y();
    const double dz = a.z() - b.z();
    if (std::sqrt(dx * dx + dy * dy + dz * dz) > mIKPositionTol) return false;

    return std::fabs(angularDelta(a.theta_x(), b.theta_x())) <= mIKOrientationTol
        && std::fabs(angularDelta(a.theta_y(), b.theta_y())) <= mIKOrientationTol
        && std::fabs(angularDelta(a.theta_z(), b.theta_z())) <= mIKOrientationTol;
}


bool Controller::solveIK(const k_api::Base::Pose& target, k_api::Base::JointAngles& ik_solution_out, std::string& error_out)
{
    // The Kortex solver is deterministic for a given seed, so a single call just returns whatever
    // branch the current configuration happens to sit in - sometimes an awkward one. Instead we
    // solve many times (first from the measured configuration, then from random seeds), keep every
    // distinct solution, and take the one that moves the joints the least. Candidates are checked
    // with FK before being accepted, cheapest first, so a seed that converged somewhere else can't
    // win by being "close".
    k_api::Base::JointAngles measured;
    try {
        std::lock_guard<std::mutex> lock(mApiMutex);
        measured = mBase->GetMeasuredJointAngles();
    } catch (std::exception& ex) {
        error_out = ex.what();
        return false;
    }
    const int joint_count = measured.joint_angles_size();
    if (joint_count == 0) {
        error_out = "could not read measured joint angles";
        return false;
    }

    k_api::Base::IKData ik_data;
    auto* pose = ik_data.mutable_cartesian_pose();
    pose->set_x(target.x());             pose->set_y(target.y());
    pose->set_z(target.z());             pose->set_theta_x(target.theta_x());
    pose->set_theta_y(target.theta_y()); pose->set_theta_z(target.theta_z());

    auto* guess = ik_data.mutable_guess();
    for (int i = 0; i < joint_count; ++i) {
        auto* g = guess->add_joint_angles();
        g->set_joint_identifier(measured.joint_angles(i).joint_identifier());
        g->set_value(measured.joint_angles(i).value());
    }

    std::uniform_real_distribution<double> full_circle(0.0, 360.0);
    std::vector<std::pair<double, k_api::Base::JointAngles>> candidates;
    std::string last_error;

    for (int attempt = 0; attempt < mIKAttempts; ++attempt) {
        if (attempt > 0) {
            // random restart - the measured configuration is only tried once
            for (int i = 0; i < joint_count; ++i) {
                guess->mutable_joint_angles(i)->set_value(static_cast<float>(full_circle(mRng)));
            }
        }

        k_api::Base::JointAngles solution;
        try {
            std::lock_guard<std::mutex> lock(mApiMutex);
            solution = mBase->ComputeInverseKinematics(ik_data);
        } catch (std::exception& ex) {
            last_error = ex.what();
            continue;   // this seed did not converge, try another
        }
        if (solution.joint_angles_size() < joint_count) continue;

        double cost = jointMotionCost(measured, solution);

        // drop duplicates of a branch we already have
        bool duplicate = false;
        for (const auto& c : candidates) {
            if (jointMotionCost(c.second, solution) < 1.0) { duplicate = true; break; }
        }
        if (duplicate) continue;

        candidates.emplace_back(cost, solution);
    }

    if (candidates.empty()) {
        error_out = last_error.empty() ? "IK did not converge" : ("IK did not converge: " + last_error);
        return false;
    }

    std::sort(candidates.begin(), candidates.end(),
              [](const auto& a, const auto& b) { return a.first < b.first; });

    // verify cheapest-first so we normally pay for a single FK call
    for (const auto& candidate : candidates) {
        k_api::Base::Pose reached;
        try {
            std::lock_guard<std::mutex> lock(mApiMutex);
            reached = mBase->ComputeForwardKinematics(candidate.second);
        } catch (std::exception& ex) {
            last_error = ex.what();
            continue;
        }
        if (!poseMatches(reached, target)) continue;

        RCLCPP_INFO(this->get_logger(),
                    "IK: %zu distinct solutions from %d attempts, chose one with %.1f deg of total joint motion",
                    candidates.size(), mIKAttempts, candidate.first);
        ik_solution_out = candidate.second;
        return true;
    }

    error_out = "no IK solution reached the target pose (" + std::to_string(candidates.size()) + " candidates rejected)";
    return false;
}


// --- Compute IK Service ---
void Controller::handleComputeIK(
    const std::shared_ptr<ros2_interfaces::srv::ComputeIK::Request> request,
    std::shared_ptr<ros2_interfaces::srv::ComputeIK::Response> response)
{
    k_api::Base::Pose target;
    target.set_x(request->x);
    target.set_y(request->y);
    target.set_z(request->z);
    target.set_theta_x(request->theta_x);
    target.set_theta_y(request->theta_y);
    target.set_theta_z(request->theta_z);

    k_api::Base::JointAngles ik_solution;
    std::string ik_error;
    response->success = solveIK(target, ik_solution, ik_error);

    if (!response->success) {
        RCLCPP_ERROR(this->get_logger(), "ComputeIK failed: %s", ik_error.c_str());
    }
}


// --- Set Tool Length Service ---
void Controller::handleSetToolLength(
    const std::shared_ptr<ros2_interfaces::srv::SetToolLength::Request> request,
    std::shared_ptr<ros2_interfaces::srv::SetToolLength::Response> response)
{
    std::string error;
    response->success = setToolLength(request->length, error);
    if (response->success) {
        char msg[48];
        snprintf(msg, sizeof(msg), "tool length %.3f m", request->length);
        response->message = msg;
    } else {
        RCLCPP_ERROR(this->get_logger(), "SetToolLength failed: %s", error.c_str());
        response->message = error;
    }
}


bool Controller::setToolLength(float length, std::string& error_out)
{
    // Kortex moves its tool frame immediately, so measured poses, reach_pose actions and
    // twists all refer to the new TCP from here on. Only call this with the arm still.
    k_api::ControlConfig::ToolConfiguration tool(mBareTool);
    tool.mutable_tool_transform()->set_z(mBareTool.tool_transform().z() + length);

    try {
        std::lock_guard<std::mutex> lock(mApiMutex);
        mControlConfig->SetToolConfiguration(tool);
    } catch (std::exception& ex) {
        error_out = std::string("Kortex error: ") + ex.what();
        return false;
    }

    mToolLength = length;
    RCLCPP_INFO(this->get_logger(), "Tool TCP set %.2f m past the bare gripper (tool z = %.2f m)",
                length, tool.tool_transform().z());
    return true;
}


float Controller::get_gripper_position()
{
    try {
        k_api::Base::GripperRequest request;
        request.set_mode(k_api::Base::GripperMode::GRIPPER_POSITION);
        auto measured = mBase->GetMeasuredGripperMovement(request);
        if (measured.finger_size() > 0) {
            return measured.finger(0).value(); // returns 0 (open) to 1 (closed)
        }
    } catch (...) {}
    return 0.0f;
}


void Controller::toolZAxis(float theta_x, float theta_y, float theta_z, double out[3]) const
{
    // to get the tool's Z-axis in the base frame, given the extrensic XYZ angles from Kortex GetMeasuredCartesianPose
    const double tx = theta_x * M_PI / 180.0;
    const double ty = theta_y * M_PI / 180.0;
    const double tz = theta_z * M_PI / 180.0;

    // only worried about the Z-axis (third column of R=Rz*Ry*Rx rotation matrix)
    out[0] = cos(tz) * sin(ty) * cos(tx) + sin(tz) * sin(tx);
    out[1] = sin(tz) * sin(ty) * cos(tx) - cos(tz) * sin(tx);
    out[2] = cos(ty) * cos(tx);
}


k_api::Base::Pose Controller::shiftAlongToolZ(const k_api::Base::Pose& pose, double distance) const
{
    // finds the XYZ coordinates (relative to the base) of a point along the tool's Z axis
    double z_tool[3];
    toolZAxis(pose.theta_x(), pose.theta_y(), pose.theta_z(), z_tool);

    k_api::Base::Pose shifted(pose);
    shifted.set_x(pose.x() + distance * z_tool[0]);
    shifted.set_y(pose.y() + distance * z_tool[1]);
    shifted.set_z(pose.z() + distance * z_tool[2]);
    return shifted;
}


// --- Command Gripper Action Execution ---
void Controller::execute_gripper(const std::shared_ptr<GoalHandleCommandGripper> goal_handle)
{
    const auto goal = goal_handle->get_goal();
    auto result = std::make_shared<ros2_interfaces::action::CommandGripper::Result>();
    float target_position = goal->position;

    k_api::Base::GripperCommand command;
    command.set_mode(k_api::Base::GripperMode::GRIPPER_POSITION);
    auto finger = command.mutable_gripper()->add_finger();
    finger->set_finger_identifier(1);
    finger->set_value(target_position);

    try {
        {
            std::lock_guard<std::mutex> lock(mApiMutex); // make sure only one thread is sending commands to the robot at a time
            mBase->SendGripperCommand(command);
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(1500)); // wait for the gripper to finish moving

        // check if gripper reached intended position
        float current_position;
        {
            std::lock_guard<std::mutex> lock(mApiMutex);
            current_position = get_gripper_position();
        }
        
        // intentionally not qualifying success as reaching the targegt position, because the gripper may have closed around an object and not reached the target
        RCLCPP_INFO(this->get_logger(), "Gripper command executed: current=%.2f, target=%.2f", current_position, target_position);

        char position_msg[32];
        snprintf(position_msg, sizeof(position_msg), "gripper at %.2f", current_position);
        result->success = true;
        result->message = position_msg;
        goal_handle->succeed(result);

    } catch (std::exception& ex) {
        RCLCPP_ERROR(this->get_logger(), "Gripper command failed: %s", ex.what());
        result->success = false;
        result->message = std::string("Kortex error: ") + ex.what();
        goal_handle->abort(result);
    } catch (...) {
        RCLCPP_ERROR(this->get_logger(), "Gripper command failed.");
        result->success = false;
        result->message = "gripper command failed";
        goal_handle->abort(result);
    }
}

// --- Move Straight Action Execution ---
void Controller::execute_straight(const std::shared_ptr<GoalHandleMoveStraight> goal_handle)
{
    const auto goal = goal_handle->get_goal();
    auto result = std::make_shared<ros2_interfaces::action::MoveStraight::Result>();

    k_api::Base::Pose current;
    try {
        std::lock_guard<std::mutex> lock(mApiMutex);
        current = mBase->GetMeasuredCartesianPose();
    } catch (std::exception& ex) {
        RCLCPP_ERROR(this->get_logger(), "Kortex Error reading pose for MoveStraight: %s", ex.what());
        result->success = false;
        result->message = std::string("Kortex error: ") + ex.what();
        goal_handle->abort(result);
        return;
    }

    const k_api::Base::Pose target = shiftAlongToolZ(current, goal->distance);

    k_api::Base::Action action;
    action.set_name("MoveStraight (Tool Z Axis)");
    auto* constrained_pose = action.mutable_reach_pose();
    auto* pose = constrained_pose->mutable_target_pose();
    pose->set_x(target.x());
    pose->set_y(target.y());
    pose->set_z(target.z());

    // keep same orientation as current pose (only translate)
    pose->set_theta_x(current.theta_x());
    pose->set_theta_y(current.theta_y());
    pose->set_theta_z(current.theta_z());

    try {
        {
            std::lock_guard<std::mutex> lock(mApiMutex);
            mBase->ExecuteAction(action);
        }

        RCLCPP_INFO(this->get_logger(), "Moving %.3f m along tool +Z", goal->distance);

        const double timeout_s = 5;

        std::string poll_message;
        auto outcome = pollUntilCartesianTarget<MoveStraight>(goal_handle, target.x(), target.y(), target.z(), "MoveStraight (Tool +Z)", timeout_s, poll_message);

        result->message = poll_message;

        if (outcome == PollOutcome::CANCELLED) {
            result->success = false;
            goal_handle->canceled(result);
            return;
        }
        if (outcome != PollOutcome::REACHED) {
            result->success = false;
            goal_handle->abort(result); // canceled() is only valid after a cancel request
            return;
        }

        RCLCPP_INFO(this->get_logger(), "Straight-line movement complete");
        result->success = true;
        goal_handle->succeed(result);
    } catch (k_api::KDetailedException& ex) {
        RCLCPP_ERROR(this->get_logger(), "Kortex Error during MoveStraight (Tool Z Axis): %s", ex.what());
        result->success = false;
        result->message = std::string("Kortex error: ") + ex.what();
        goal_handle->abort(result);
    }
}

// --- Move to Joints Action Execution ---
void Controller::execute_joints(const std::shared_ptr<GoalHandleMoveToJoints> goal_handle)
{
    const auto goal = goal_handle->get_goal();
    auto result = std::make_shared<ros2_interfaces::action::MoveToJoints::Result>();

    std::vector<double> target_joints;

    RCLCPP_INFO(this->get_logger(), "Moving to desired joint configuration...");

    if (goal->joint_angles.size() < 7) { // sanity check
        RCLCPP_ERROR(this->get_logger(), "Joint target has invalid joint count: %zu (expected 7)", goal->joint_angles.size());
        result->success = false;
        result->message = "invalid joint count";
        goal_handle->abort(result);
        return;
    }

    k_api::Base::Action action;
    action.set_name("MoveToJoints");
    auto reach_joints = action.mutable_reach_joint_angles();
    auto joints = reach_joints->mutable_joint_angles();

    // construct message
    for (size_t i = 0; i < 7; ++i) {
        auto j = joints->add_joint_angles();
        j->set_joint_identifier(i);
        j->set_value(static_cast<float>(goal->joint_angles[i]));
    }

    target_joints.resize(7);
    for (size_t j = 0; j < 7; ++j) {
        target_joints[j] = goal->joint_angles[j];
    }

    try {
        {
            std::lock_guard<std::mutex> lock(mApiMutex);
            mBase->ExecuteAction(action);
        }
    } catch (k_api::KDetailedException& ex) {
        RCLCPP_ERROR(this->get_logger(), "Kortex Error during MoveToJoints: %s", ex.what());
        result->success = false;
        result->message = std::string("Kortex error: ") + ex.what();
        goal_handle->abort(result);
        return;
    }

    // Polling / completion check loop
    const double timeout_s = 30.0;
    const auto start = std::chrono::steady_clock::now();
    k_api::Base::JointAngles current_joints;

    try {
        while (rclcpp::ok()) {
            if (goal_handle->is_canceling()) {
                std::lock_guard<std::mutex> lock(mApiMutex);
                mBase->Stop();
                result->success = false;
                result->message = "canceled";
                goal_handle->canceled(result);
                return;
            }

            if (mStopRequested) {
                // the /stop callback already halted the arm, but stop again in case this goal started after it
                {
                    std::lock_guard<std::mutex> lock(mApiMutex);
                    mBase->Stop();
                }
                RCLCPP_WARN(this->get_logger(), "MoveToJoints stopped by /stop.");
                result->success = false;
                result->message = "stopped by /stop";
                goal_handle->abort(result); // canceled() is only valid after a cancel request
                return;
            }

            {
                std::lock_guard<std::mutex> lock(mApiMutex);
                current_joints = mBase->GetMeasuredJointAngles();
            }

            float max_diff = 0;
            for (int i = 0; i < 7; ++i) {
                float target = static_cast<float>(target_joints[i]);
                float actual = current_joints.joint_angles(i).value();
                float diff = std::abs(target - actual);
                // Handle 360 degree wrapping (1 degree and 359 degrees are close)
                if (diff > 180.0f) {
                    diff = 360.0f - diff;
                }
                max_diff = std::max(max_diff, diff);
            }

            if (max_diff < 1.0f) break; // 1 degree threshold for reliable completion

            double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
            if (elapsed > timeout_s) {
                {
                    std::lock_guard<std::mutex> lock(mApiMutex);
                    mBase->Stop();
                }
                RCLCPP_ERROR(this->get_logger(), "MoveToJoints timed out after %.1f s - stopping the arm.", elapsed);
                result->success = false;
                result->message = "timed out after " + std::to_string(static_cast<int>(elapsed)) + " s";
                goal_handle->abort(result);
                return;
            }

            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        if (current_joints.joint_angles_size() < 7) { // never polled (node shutting down)
            result->success = false;
            result->message = "node shutting down";
            goal_handle->abort(result);
            return;
        }

        RCLCPP_INFO(this->get_logger(), "Joint movement complete. Joint angles: (%.1f, %.1f, %.1f, %.1f, %.1f, %.1f, %.1f)",
            current_joints.joint_angles(0).value(), current_joints.joint_angles(1).value(),
            current_joints.joint_angles(2).value(), current_joints.joint_angles(3).value(),
            current_joints.joint_angles(4).value(), current_joints.joint_angles(5).value(),
            current_joints.joint_angles(6).value());
        result->success = true;
        result->message = "reached";
        goal_handle->succeed(result);
    } catch (k_api::KDetailedException& ex) {
        RCLCPP_ERROR(this->get_logger(), "Kortex Error during MoveToJoints: %s", ex.what());
        result->success = false;
        result->message = std::string("Kortex error: ") + ex.what();
        goal_handle->abort(result);
    }
}

// --- Move to Pose Action Execution ---
void Controller::execute_pose(const std::shared_ptr<GoalHandleMoveToPose> goal_handle)
{
    /*
    We will actually get to desired Cartesian poses through joint space. This works by calculating IK of the target pose, then sending a joint-space move command to the robot.
    Because we often keep the orientation of the tool fixed from current to target, this approach allows for smoother motion rather than locking orientation throughout the movement.
    */
    const auto goal = goal_handle->get_goal();
    auto result = std::make_shared<ros2_interfaces::action::MoveToPose::Result>();

    k_api::Base::Pose target;
    target.set_x(goal->x);
    target.set_y(goal->y);
    target.set_z(goal->z);
    target.set_theta_x(goal->theta_x);
    target.set_theta_y(goal->theta_y);
    target.set_theta_z(goal->theta_z);

    k_api::Base::JointAngles ik_solution;
    std::string ik_error;

    if (!solveIK(target, ik_solution, ik_error)) {
        RCLCPP_ERROR(this->get_logger(), "IK found no solution - aborting move: %s", ik_error.c_str());
        result->success = false;
        result->message = "IK found no solution for target pose";
        goal_handle->abort(result);
        return;
    }

    k_api::Base::Action action;
    action.set_name("ExecutePose (joint space)");
    *action.mutable_reach_joint_angles()->mutable_joint_angles() = ik_solution;

    try {
        {
            std::lock_guard<std::mutex> lock(mApiMutex);
            mBase->ExecuteAction(action);
        }

        RCLCPP_INFO(this->get_logger(),"Moving to pose target: (%.3f, %.3f, %.3f)",goal->x, goal->y, goal->z);

        std::string poll_message;
        auto outcome = pollUntilCartesianTarget<MoveToPose>(goal_handle, target.x(), target.y(), target.z(), "ExecutePose (joint space)", 15.0, poll_message);

        result->message = poll_message;

        if (outcome == PollOutcome::CANCELLED) {
            result->success = false;
            goal_handle->canceled(result);
            return;
        }
        if (outcome != PollOutcome::REACHED) {
            result->success = false;
            goal_handle->abort(result); // canceled() is only valid after a cancel request
            return;
        }

        RCLCPP_INFO(this->get_logger(), "Pose target reached.");
        result->success = true;
        goal_handle->succeed(result);
    } catch (k_api::KDetailedException& ex) {
        RCLCPP_ERROR(this->get_logger(), "Kortex Error: %s", ex.what());
        result->success = false;
        result->message = std::string("Kortex error: ") + ex.what();
        goal_handle->abort(result);
    }

}

// --- Servo to Target Action Execution ---
void Controller::execute_servo(const std::shared_ptr<GoalHandleServoToTarget> goal_handle)
{
    /*
    The caller measures the target and streams the vector from the TCP to it on /servo_vector. Each new
    vector becomes one base-frame twist (PD on the vector, capped at servo_max_speed), and the goal succeeds
    once the vector is shorter than stop_distance. Kortex keeps executing the last twist until it gets
    another command, so every way out of here stops the arm - including the vector stream going quiet.
    */
    const auto goal = goal_handle->get_goal();
    auto result = std::make_shared<ServoToTarget::Result>();
    auto feedback = std::make_shared<ServoToTarget::Feedback>();
    using clock = std::chrono::steady_clock;
    auto seconds = [](clock::duration d) { return std::chrono::duration<double>(d).count(); };

    auto stop_arm = [this]() {
        std::lock_guard<std::mutex> lock(mApiMutex);
        try {
            mBase->Stop();
        } catch (std::exception& ex) {
            RCLCPP_ERROR(this->get_logger(), "Kortex error while stopping ServoToTarget: %s", ex.what());
        }
    };
    auto fail = [&](const std::string& message) {
        stop_arm();
        RCLCPP_WARN(this->get_logger(), "ServoToTarget failed: %s", message.c_str());
        result->success = false;
        result->message = message;
        goal_handle->abort(result); // canceled() is only valid after a cancel request
    };

    // vectors published before this goal don't count
    uint64_t last_seq;
    {
        std::lock_guard<std::mutex> lock(mServoMutex);
        last_seq = mServoSeq;
    }

    RCLCPP_INFO(this->get_logger(), "Servoing on /servo_vector until within %.3f m", goal->stop_distance);

    const auto start = clock::now();
    auto last_vector_time = start;   // the stream gets servo_vector_timeout_s to start, too
    bool have_prev = false;
    double prev_e[3]{0.0, 0.0, 0.0};
    clock::time_point prev_stamp;

    while (rclcpp::ok()) {
        if (goal_handle->is_canceling()) {
            stop_arm();
            RCLCPP_WARN(this->get_logger(), "ServoToTarget cancelled.");
            result->success = false;
            result->message = "canceled";
            goal_handle->canceled(result);
            return;
        }
        if (mStopRequested) {
            fail("stopped by /stop");
            return;
        }

        const auto now = clock::now();
        if (seconds(now - start) > mServoTimeout) {
            fail("timed out after " + std::to_string(static_cast<int>(mServoTimeout)) + " s");
            return;
        }

        double e[3];
        uint64_t seq;
        clock::time_point stamp;
        {
            std::lock_guard<std::mutex> lock(mServoMutex);
            std::copy(mServoVector, mServoVector + 3, e);
            seq = mServoSeq;
            stamp = mServoStamp;
        }

        if (seq == last_seq) {
            // nothing new: the last twist is still running, as long as the stream is alive
            if (seconds(now - last_vector_time) > mServoVectorTimeout) {
                fail(have_prev ? "no new /servo_vector for " + std::to_string(mServoVectorTimeout) + " s"
                               : "no /servo_vector received");
                return;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
            continue;
        }
        last_seq = seq;
        last_vector_time = stamp;

        const double dist = std::sqrt(e[0] * e[0] + e[1] * e[1] + e[2] * e[2]);
        if (!std::isfinite(dist)) {   // NaN fails every comparison below and Kortex ignores the twist
            fail("non-finite /servo_vector");
            return;
        }
        feedback->distance_to_go = static_cast<float>(dist);
        goal_handle->publish_feedback(feedback);

        if (dist < goal->stop_distance) {
            stop_arm();
            RCLCPP_INFO(this->get_logger(), "ServoToTarget reached (%.3f m to go).", dist);
            result->success = true;
            result->message = "reached";
            goal_handle->succeed(result);
            return;
        }

        // PD on the vector; the derivative uses the time between vectors, not the loop period
        double v[3];
        const double dt = have_prev ? seconds(stamp - prev_stamp) : 0.0;
        for (int i = 0; i < 3; ++i) {
            v[i] = mServoKp * e[i];
            if (dt > 1e-3) v[i] += mServoKd * (e[i] - prev_e[i]) / dt;
            prev_e[i] = e[i];
        }
        prev_stamp = stamp;
        have_prev = true;

        const double speed = std::sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
        if (speed > mServoMaxSpeed) {
            for (double& vi : v) vi *= mServoMaxSpeed / speed;
        }

        k_api::Base::TwistCommand command;
        command.set_reference_frame(k_api::Common::CARTESIAN_REFERENCE_FRAME_BASE);
        auto* twist = command.mutable_twist();   // angular stays 0: translation only
        twist->set_linear_x(static_cast<float>(v[0]));
        twist->set_linear_y(static_cast<float>(v[1]));
        twist->set_linear_z(static_cast<float>(v[2]));
        RCLCPP_INFO_THROTTLE(this->get_logger(), *this->get_clock(), 1000,
            "Servo: %.3f m to go, vector (%.3f, %.3f, %.3f), twist (%.3f, %.3f, %.3f) m/s",
            dist, e[0], e[1], e[2], v[0], v[1], v[2]);

        try {
            std::lock_guard<std::mutex> lock(mApiMutex);
            mBase->SendTwistCommand(command);
        } catch (std::exception& ex) {
            fail(std::string("Kortex error: ") + ex.what());
            return;
        }
    }

    fail("node shutting down");
}


int main(int argc, char** argv)
{
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<Controller>());
    rclcpp::shutdown();
    return 0;
}

