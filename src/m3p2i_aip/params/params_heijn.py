import torch

# Paramters in the reactive_tamp file
allow_viewer = False
task = "navigation"
num_envs = 100
nx = 6
tensor_args = {'device':"cuda:0", 'dtype':torch.float32} 
noise_sigma = torch.tensor([[3, 0, 0], [0, 3, 0], [0, 0, 5]], **tensor_args)
horizon = 20
lambda_ = 1 
u_max = torch.tensor([1.5, 1.5, 3.5], **tensor_args)
u_min = torch.tensor([-1.5, -1.5, -3.5], **tensor_args)
step_dependent_dynamics = True
terminal_state_cost = None
sample_null_action = True
use_priors = False
u_per_command = 20
filter_u = True

# Parameters in the sim file
sim_allow_viewer = True
sim_num_envs = 1 
dt = 0.05
block_index = 7
kp_suction = 400

# Parameters in common
robot = "heijn"                            # choose from "point_robot", "boxer", "albert"
environment_type = "normal"                   # choose from "normal", "battery", "lab"
spacing = 10.0
manual_control_type = "vel_control"        # choose from "vel_control", "pos_control", "force_control"
suction_active = False                     # the same with use_vacuum
print_flag = False