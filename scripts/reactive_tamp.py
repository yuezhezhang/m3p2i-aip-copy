from isaacgym import gymapi
from isaacgym import gymutil
from isaacgym import gymtorch
import torch
from fusion_mppi import mppi, fusion_mppi
from utils import env_conf, sim_init, data_transfer
from params import params_utils
import time
import copy
import socket, io
import numpy as np
torch.set_printoptions(precision=3, sci_mode=False, linewidth=160)

class PLANNER_PATROLLING:
    def __init__(self, goals) -> None:
        self.goals = goals
        self.goal_id = 0
        self.curr_goal = self.goals[self.goal_id]
        self.task = "navigation"
    
    def update_goal(self):
        self.goal_id += 1
        if self.goal_id >= self.goals.size(0):
            self.goal_id = 0
        self.curr_goal = self.goals[self.goal_id]


class REACTIVE_TAMP:
    def __init__(self, params) -> None:
        # Make the environment and simulation
        self.allow_viewer = params.allow_viewer
        self.visualize_rollouts = params.visualize_rollouts
        self.num_envs = params.num_envs
        self.spacing = params.spacing
        self.robot = params.robot
        self.environment_type = params.environment_type
        self.gym, self.sim, self.viewer, envs, _ = sim_init.make(self.allow_viewer, self.num_envs, self.spacing, self.robot, self.environment_type)

        # Acquire states
        self.dof_states, self.num_dofs, self.num_actors, self.root_states = sim_init.acquire_states(self.gym, self.sim, print_flag=False)
        actors_per_env = int(self.num_actors/self.num_envs)
        bodies_per_env = self.gym.get_env_rigid_body_count(envs[0])

        # Choose the task planner
        self.task = params.task
        if self.task == "Patrolling":
            self.task_planner = PLANNER_PATROLLING(goals = torch.tensor([[-3, -3], [3, -3], [3, 3], [-3, 3]], device="cuda:0"))

        # Choose the motion planner
        self.motion_planner = fusion_mppi.FUSION_MPPI(
                            dynamics = None, 
                            running_cost = None, 
                            nx = params.nx, 
                            noise_sigma = params.noise_sigma,
                            num_samples = self.num_envs, 
                            horizon = params.horizon,
                            lambda_ = params.lambda_, 
                            device = params.device, 
                            u_max = params.u_max,
                            u_min = params.u_min,
                            step_dependent_dynamics = params.step_dependent_dynamics,
                            terminal_state_cost = params.terminal_state_cost,
                            sample_null_action = params.sample_null_action,
                            use_priors = params.use_priors,
                            use_vacuum = params.suction_active,
                            robot_type = self.robot,
                            u_per_command = params.u_per_command,
                            actors_per_env = actors_per_env,
                            env_type = self.environment_type,
                            bodies_per_env = bodies_per_env,
                            filter_u = params.filter_u
                            )
        
        # Make sure the socket does not already exist
        self.server_address = './uds_socket'
        data_transfer.check_server(self.server_address)

    def tamp_interface(self, curr_pos):
        # Update task planner goal
        if torch.norm(curr_pos - self.task_planner.curr_goal) < 0.1:
            self.task_planner.update_goal()
        # Send task and goal to motion planner
        self.motion_planner.update_task(self.task_planner.task)
        self.motion_planner.update_nav_goal(self.task_planner.curr_goal)

    def run(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            # Build the connection
            s.bind(self.server_address)
            s.listen()
            conn, addr = s.accept()
            with conn:
                print(f"Connected by {addr}")
                i=0
                while True:
                    i+=1
                    # Receive dof states
                    res = conn.recv(2**14)
                    r = copy.copy(res)
                    _dof_states = data_transfer.bytes_to_torch(r).repeat(self.num_envs, 1)
                    conn.sendall(b"next please")

                    # Receive root states
                    res = conn.recv(2**14)
                    r = copy.copy(res)
                    _root_states = data_transfer.bytes_to_torch(r).repeat(self.num_envs, 1)

                    # Reset the simulator to requested state
                    if self.robot == "point_robot" or "boxer":
                        s = _dof_states.view(-1, 4) # [x, v_x, y, v_y]
                    elif self.robot == "heijn":
                        s = _dof_states.view(-1, 6) # [x, v_x, y, v_y, theta, v_theta]
                    self.gym.set_dof_state_tensor(self.sim, gymtorch.unwrap_tensor(s))
                    self.gym.set_actor_root_state_tensor(self.sim, gymtorch.unwrap_tensor(_root_states))
                    sim_init.refresh_states(self.gym, self.sim)

                    # Update TAMP interface
                    if self.robot == "boxer":
                        robot_pos = _root_states[-1, :2]
                    elif self.robot == "point_robot" or "heijn":
                        robot_pos = torch.tensor([s[0][0], s[0][2]], device="cuda:0")
                    if self.task == "Patrolling":
                        self.tamp_interface(robot_pos)

                    # Update gym in mppi
                    self.motion_planner.update_gym(self.gym, self.sim, self.viewer)

                    # Compute optimal action and send to real simulator
                    actions = self.motion_planner.command(s[0])
                    conn.sendall(data_transfer.torch_to_bytes(actions))

                    # Visualize rollouts
                    if self.visualize_rollouts:
                        # Get the rollouts trajectory
                        rollouts = self.motion_planner.states[0, :, :, :].cpu().clone().numpy()
                        current_traj = np.zeros((self.motion_planner.T, 2))
                        K = self.motion_planner.K
                        res = conn.recv(1024)
                        conn.sendall(data_transfer.numpy_to_bytes(self.motion_planner.K))
                        for i in range(K):
                            res4 = conn.recv(1024)
                            current_traj[:, 1] = rollouts[i][:, 0]     # x pos
                            current_traj[:, 0] = rollouts[i][:, 2]     # y pos
                            conn.sendall(data_transfer.numpy_to_bytes(current_traj))

if __name__== "__main__":
    params = params_utils.load_params()
    reactive_tamp = REACTIVE_TAMP(params)
    reactive_tamp.run()