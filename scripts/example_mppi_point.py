from isaacgym import gymapi
from isaacgym import gymutil
from isaacgym import gymtorch
import torch
from pytorch_mppi import mppi
from utils import env_conf, sim_init
import time
torch.set_printoptions(precision=3, sci_mode=False, linewidth=160)

# Make the environment and simulation
allow_viewer = True
num_envs = 1000
spacing = 10.0
robot = "point_robot"               # choose from "point_robot", "boxer", "albert"
obstacle_type = "normal"            # choose from "normal", "battery"
control_type = "vel_control"        # choose from "vel_control", "pos_control", "force_control"
gym, sim, viewer, envs, robot_handles = sim_init.make(allow_viewer, num_envs, spacing, robot, obstacle_type, control_type)

# Acquire states
dof_states, num_dofs, num_actors, root_states = sim_init.acquire_states(gym, sim, print_flag=False)

# Save copies of states, in order to reset the rollout of MPPI
saved_dof_states = dof_states.clone().view(-1, 4)
saved_root_states = root_states.clone()

def mppi_dynamics(input_state, action, t):
    if t == 0:    
        gym.set_dof_state_tensor(sim, gymtorch.unwrap_tensor(input_state))
    gym.set_dof_velocity_target_tensor(sim, gymtorch.unwrap_tensor(action))
    gym.simulate(sim)
    gym.fetch_results(sim, True)
    gym.refresh_dof_state_tensor(sim)

    res = torch.clone(dof_states).view(-1, 4)
    return res

def running_cost(state, action):
    # State: for each environment, the current state containing position and velocity
    # Action: same but for control input
    
    state_pos = torch.cat((state[:, 0].unsqueeze(1), state[:, 2].unsqueeze(1)), 1)
    task_cost = torch.linalg.norm(state_pos - torch.tensor([3, 3], device="cuda:0"), axis=1)
    
    w_u = 0.01 # Weight for control input, more dominant when close to the goal
    control_cost = torch.sum(torch.square(action),1)
    
    # Contact forces
    _net_cf = gym.acquire_net_contact_force_tensor(sim)
    net_cf = gymtorch.wrap_tensor(_net_cf)
    _net_cf = gym.refresh_net_contact_force_tensor(sim)
    # Take only forces in x,y in modulus for each environment. Avoid all collisions
    net_cf = torch.sum(torch.abs(torch.cat((net_cf[:, 0].unsqueeze(1), net_cf[:, 1].unsqueeze(1)), 1)),1)
    coll_cost = torch.sum(net_cf.reshape([num_envs, int(net_cf.size(dim=0)/num_envs)]), 1)
    w_c = 100000 # Weight for collisions
    # Binary check for collisions. So far checking all collision of all actors. TODO: check collision of robot body only       
    coll_cost[coll_cost>0.1] = 1
    coll_cost[coll_cost<=0.1] = 0

    return  task_cost + w_u*control_cost + w_c*coll_cost

def terminal_state_cost(states, actions):
    # States: for each environment and for the whole time horizon, the state trajectory containing position and velocity
    # Actions: same but for control input
    return torch.zeros(num_envs, device="cuda:0")

mppi = mppi.MPPI(
    dynamics=mppi_dynamics, 
    running_cost=running_cost, 
    nx=2, 
    noise_sigma = torch.tensor([[5, 0], [0, 5]], device="cuda:0", dtype=torch.float32),
    num_samples=num_envs, 
    horizon=10,
    lambda_=0.1, 
    device="cuda:0", 
    u_max=torch.tensor([3.0, 3.0]),
    u_min=torch.tensor([-3.0, -3.0]),
    step_dependent_dynamics=True,
    terminal_state_cost=terminal_state_cost
    )

# time logging
frame_count = 0
next_fps_report = 2.0
t1 = 0

while viewer is None or not gym.query_viewer_has_closed(viewer):

    # Take saved real_state in correct format for mppi.
    s = saved_dof_states.view(-1, 4)[0] # [x, v_x, y, v_y]

    # Compute mppi action. This will internally use the simulator to rollout the dynamics.
    action = mppi.command(s)
    all_actions = torch.zeros(num_envs * 2, device="cuda:0")
    all_actions[:2] = action

    # Reset the simulator to saves
    gym.set_dof_state_tensor(sim, gymtorch.unwrap_tensor(saved_dof_states))
    gym.set_actor_root_state_tensor(sim, gymtorch.unwrap_tensor(saved_root_states))

    # Apply real action. (same action for all envs).
    gym.set_dof_velocity_target_tensor(sim, gymtorch.unwrap_tensor(all_actions))

    # Step the simulation
    sim_init.step(gym, sim)
    sim_init.refresh_states(gym, sim)

    # Update saves
    saved_dof_states = torch.clone(dof_states).view(-1, 4)
    saved_root_states = torch.clone(root_states)
    
    # Step rendering
    sim_init.step_rendering(gym, sim, viewer)
    next_fps_report, frame_count, t1 = sim_init.time_logging(gym, sim, next_fps_report, frame_count, t1, num_envs)

# Destroy the simulation
sim_init.destroy_sim(gym, sim, viewer)