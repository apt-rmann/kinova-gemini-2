#pragma once

#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <std_msgs/msg/empty.hpp>
#include <std_msgs/msg/float64_multi_array.hpp>
#include <sensor_msgs/msg/joint_state.hpp>


// Kortex API
#include <BaseClientRpc.h>
#include <BaseCyclicClientRpc.h>
#include <ControlConfigClientRpc.h>
#include <SessionManager.h>
#include <RouterClient.h>
#include <TransportClientTcp.h>
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <mutex>
#include <string>
#include <unordered_map>
#include <chrono>
#include <thread>
#include <cmath>
#include <random>
#include <utility>
#include <vector>

// Custom Interfaces
#include "ros2_interfaces/action/command_gripper.hpp"
#include "ros2_interfaces/action/move_straight.hpp"
#include "ros2_interfaces/action/move_to_joints.hpp"
#include "ros2_interfaces/action/move_to_pose.hpp"
#include "ros2_interfaces/action/servo_to_target.hpp"
#include "ros2_interfaces/msg/robot_state.hpp"
#include "ros2_interfaces/srv/compute_ik.hpp"
#include "ros2_interfaces/srv/set_tool_length.hpp"



namespace k_api = Kinova::Api;

class Controller : public rclcpp::Node
{
public:
    // how we interact with Kortex API
    using CommandGripper = ros2_interfaces::action::CommandGripper;
    using GoalHandleCommandGripper = rclcpp_action::ServerGoalHandle<CommandGripper>;

    using MoveStraight = ros2_interfaces::action::MoveStraight;
    using GoalHandleMoveStraight = rclcpp_action::ServerGoalHandle<MoveStraight>;

    using MoveToJoints = ros2_interfaces::action::MoveToJoints;
    using GoalHandleMoveToJoints = rclcpp_action::ServerGoalHandle<MoveToJoints>;

    using MoveToPose = ros2_interfaces::action::MoveToPose;
    using GoalHandleMoveToPose = rclcpp_action::ServerGoalHandle<MoveToPose>;

    using ServoToTarget = ros2_interfaces::action::ServoToTarget;
    using GoalHandleServoToTarget = rclcpp_action::ServerGoalHandle<ServoToTarget>;

    Controller();

private:
    // --- Kortex API Members ---
    k_api::TransportClientTcp* mTransport;
    k_api::RouterClient* mRouter;
    k_api::Base::BaseClient* mBase;
    k_api::BaseCyclic::BaseCyclicClient* mBaseCyclic;
    k_api::ControlConfig::ControlConfigClient* mControlConfig;

    // Mutex for thread-safe API access
    std::mutex mApiMutex;

    // Kortex tool configuration of the bare gripper (read at startup); an attached tool
    // only adds its length to this transform's z, so mass and center of mass are untouched
    k_api::ControlConfig::ToolConfiguration mBareTool;
    std::atomic<float> mToolLength{0.0f};   // meters past the bare-gripper TCP along tool +Z

    // set by the /stop subscriber, checked by every motion polling loop
    std::atomic<bool> mStopRequested{false};

    // Action Servers
    rclcpp_action::Server<CommandGripper>::SharedPtr CommandGripperServer;
    rclcpp_action::Server<MoveStraight>::SharedPtr MoveStraightServer;
    rclcpp_action::Server<MoveToJoints>::SharedPtr MoveToJointsServer;
    rclcpp_action::Server<MoveToPose>::SharedPtr MoveToPoseServer;
    rclcpp_action::Server<ServoToTarget>::SharedPtr ServoToTargetServer;

    // Services
    rclcpp::Service<ros2_interfaces::srv::ComputeIK>::SharedPtr ComputeIKService;
    rclcpp::Service<ros2_interfaces::srv::SetToolLength>::SharedPtr SetToolLengthService;

    // Publishers and Timers
    rclcpp::Publisher<ros2_interfaces::msg::RobotState>::SharedPtr PubState;
    rclcpp::Publisher<sensor_msgs::msg::JointState>::SharedPtr PubJointState;
    rclcpp::TimerBase::SharedPtr Timer;

    // Subscribers
    rclcpp::Subscription<std_msgs::msg::Empty>::SharedPtr SubStop;
    rclcpp::Subscription<std_msgs::msg::Float64MultiArray>::SharedPtr SubJointVelocity;

    // finger_joint upper limit in the URDF: 0 rad open, 0.7 rad fully closed
    static constexpr double FINGER_JOINT_CLOSED_RAD = 0.7;

    // IK search: how many seeds to try, and how close FK has to land for a solution to count
    int mIKAttempts{50};
    double mIKPositionTol{0.005};      // metres
    double mIKOrientationTol{2.0};     // degrees
    std::mt19937 mRng{std::random_device{}()};

    // Publishers/Helpers
    void publishState();
    void publishJointStates(const k_api::Base::JointAngles& joints, float gripper_pos);
    void handleStop(const std_msgs::msg::Empty::SharedPtr);
    void handleJointVelocity(const std_msgs::msg::Float64MultiArray::SharedPtr);
    float get_gripper_position();

    // Action Execution Functions
    void execute_gripper(const std::shared_ptr<GoalHandleCommandGripper>);
    void execute_straight(const std::shared_ptr<GoalHandleMoveStraight>);
    void execute_joints(const std::shared_ptr<GoalHandleMoveToJoints>);
    void execute_pose(const std::shared_ptr<GoalHandleMoveToPose>);
    void execute_servo(const std::shared_ptr<GoalHandleServoToTarget>);

    // Action Goal/Cancel/Accepted Callbacks
    rclcpp_action::GoalResponse handle_gripper_goal(const rclcpp_action::GoalUUID &, std::shared_ptr<const CommandGripper::Goal>);
    rclcpp_action::CancelResponse handle_gripper_cancel(const std::shared_ptr<GoalHandleCommandGripper>);
    void handle_gripper_accepted(const std::shared_ptr<GoalHandleCommandGripper>);

    rclcpp_action::GoalResponse handle_straight_goal(const rclcpp_action::GoalUUID &, std::shared_ptr<const MoveStraight::Goal>);
    rclcpp_action::CancelResponse handle_straight_cancel(const std::shared_ptr<GoalHandleMoveStraight>);
    void handle_straight_accepted(const std::shared_ptr<GoalHandleMoveStraight>);

    rclcpp_action::GoalResponse handle_joints_goal(const rclcpp_action::GoalUUID &, std::shared_ptr<const MoveToJoints::Goal>);
    rclcpp_action::CancelResponse handle_joints_cancel(const std::shared_ptr<GoalHandleMoveToJoints>);
    void handle_joints_accepted(const std::shared_ptr<GoalHandleMoveToJoints>);

    rclcpp_action::GoalResponse handle_pose_goal(const rclcpp_action::GoalUUID &, std::shared_ptr<const MoveToPose::Goal>);
    rclcpp_action::CancelResponse handle_pose_cancel(const std::shared_ptr<GoalHandleMoveToPose>);
    void handle_pose_accepted(const std::shared_ptr<GoalHandleMoveToPose>);

    rclcpp_action::GoalResponse handle_servo_goal(const rclcpp_action::GoalUUID &, std::shared_ptr<const ServoToTarget::Goal>);
    rclcpp_action::CancelResponse handle_servo_cancel(const std::shared_ptr<GoalHandleServoToTarget>);
    void handle_servo_accepted(const std::shared_ptr<GoalHandleServoToTarget>);

    // Service Callback
    void handleComputeIK(
        const std::shared_ptr<ros2_interfaces::srv::ComputeIK::Request> request,
        std::shared_ptr<ros2_interfaces::srv::ComputeIK::Response> response);
    void handleSetToolLength(
        const std::shared_ptr<ros2_interfaces::srv::SetToolLength::Request> request,
        std::shared_ptr<ros2_interfaces::srv::SetToolLength::Response> response);

    // Tool TCP
    bool setToolLength(float length, std::string& error_out);

    // Motion Helpers
    bool solveIK(const k_api::Base::Pose& target, k_api::Base::JointAngles& ik_solution_out, std::string& error_out);
    static double angularDelta(double from_deg, double to_deg);
    static double jointMotionCost(const k_api::Base::JointAngles& from, const k_api::Base::JointAngles& to);
    bool poseMatches(const k_api::Base::Pose& a, const k_api::Base::Pose& b) const;
    void toolZAxis(float theta_x, float theta_y, float theta_z, double out[3]) const;
    k_api::Base::Pose shiftAlongToolZ(const k_api::Base::Pose& pose, double distance) const;

    enum class PollOutcome { REACHED, CANCELLED, STOPPED, TIMED_OUT };

    template<typename ActionT>
    PollOutcome pollUntilCartesianTarget(
        const std::shared_ptr<rclcpp_action::ServerGoalHandle<ActionT>> goal_handle,
        double target_x, double target_y, double target_z,
        const char* label, double timeout_s, std::string& message_out);

};