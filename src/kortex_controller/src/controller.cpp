#include "kortex_controller/controller.hpp"
#include <rclcpp_action/rclcpp_action.hpp>
#include "ros2_interfaces/action/move_to_pose.hpp"
#include "ros2_interfaces/action/move_to_joints.hpp"
#include "ros2_interfaces/action/command_gripper.hpp"

using namespace std::placeholders;


Controller::Controller() : Node("kortex_controller")
{
    // Parameters
    this->declare_parameter("robot_ip", "192.168.1.10");
    this->declare_parameter("username", "admin");
    this->declare_parameter("password", "admin");
    this->declare_parameter("config_path", std::string(std::getenv("HOME") ? std::getenv("HOME") : "") + "/kinova-gemini/config.yaml");

    std::string robot_ip = this->get_parameter("robot_ip").as_string();

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

    // --- Publishers ---
    PubState = this->create_publisher<ros2_interfaces::msg::RobotState>("robot_state", 10);
    // robot_state_publisher needs this to build the TF chain through the arm's revolute joints
    PubJointState = this->create_publisher<sensor_msgs::msg::JointState>("/joint_states", 10);
    Timer = this->create_wall_timer(std::chrono::milliseconds(50), std::bind(&Controller::publishState, this));

    // --- Subscribers ---
    SubStop = this->create_subscription<std_msgs::msg::Empty>(
        "/stop", 10, std::bind(&Controller::handleStop, this, _1));

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
    // halt the arm immediately, independent of whatever action thread is running
    {
        std::lock_guard<std::mutex> lock(mApiMutex);
        try {
            mBase->Stop();
        } catch (std::exception& ex) {
            RCLCPP_ERROR(this->get_logger(), "Kortex error while stopping: %s", ex.what());
        }
    }
    mStopRequested = true;
    RCLCPP_WARN(this->get_logger(), "Stop requested on /stop - arm halted.");
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


bool Controller::solveIK(const k_api::Base::Pose& target, k_api::Base::JointAngles& ik_solution_out, std::string& error_out)
{
    // seed solver with current joint configuration
    k_api::Base::JointAngles measured;
    k_api::Base::JointAngles solution;
    try {
        std::lock_guard<std::mutex> lock(mApiMutex);
        measured = mBase->GetMeasuredJointAngles();

        k_api::Base::IKData ik_data;
        auto* pose = ik_data.mutable_cartesian_pose();
        pose->set_x(target.x());             pose->set_y(target.y());
        pose->set_z(target.z());             pose->set_theta_x(target.theta_x());
        pose->set_theta_y(target.theta_y()); pose->set_theta_z(target.theta_z());

        for (int i = 0; i < measured.joint_angles_size(); ++i) {
            auto* g = ik_data.mutable_guess()->add_joint_angles();
            g->set_joint_identifier(measured.joint_angles(i).joint_identifier());
            g->set_value(measured.joint_angles(i).value());
        }
        // simplest right now is just solving once, but it could be worth it to solve a bunch of times then choose the joint solution that is closest to current measured config
        solution = mBase->ComputeInverseKinematics(ik_data);
    } catch (std::exception& ex) {
        error_out = ex.what();
        return false;
    }

    if (solution.joint_angles_size() < measured.joint_angles_size()) {
        error_out = "IK did not converge";
        return false;
    }
    ik_solution_out = solution;
    return true;
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

// --- Servo to Target Action Execution --- not implemented yet, will buld out the rest first
void Controller::execute_servo(const std::shared_ptr<GoalHandleServoToTarget> goal_handle)
{
    auto result = std::make_shared<ros2_interfaces::action::ServoToTarget::Result>();
    result->success = false;
    result->message = "ServoToTarget is not implemented yet.";
    RCLCPP_ERROR(this->get_logger(), "%s", result->message.c_str());
    goal_handle->abort(result);
}


int main(int argc, char** argv)
{
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<Controller>());
    rclcpp::shutdown();
    return 0;
}

