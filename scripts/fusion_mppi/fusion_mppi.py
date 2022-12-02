import fusion_mppi.mppi as mppi
import torch
from isaacgym import gymtorch
from utils import sim_init

class FUSION_MPPI(mppi.MPPI):
    def __init__(self, dynamics, running_cost, nx, noise_sigma, num_samples=100, horizon=15, device="cpu", 
                    terminal_state_cost=None, 
                    lambda_=1, 
                    noise_mu=None, 
                    u_min=None, 
                    u_max=None, 
                    u_init=None, 
                    U_init=None, 
                    u_scale=1, 
                    u_per_command=1, 
                    step_dependent_dynamics=False, 
                    rollout_samples=1, 
                    rollout_var_cost=0, 
                    rollout_var_discount=0.95, 
                    sample_null_action=False, 
                    use_priors=False,
                    robot_type='point_robot',
                    noise_abs_cost=False):
        super().__init__(dynamics, running_cost, nx, noise_sigma, num_samples, horizon, device, 
                    terminal_state_cost, 
                    lambda_, 
                    noise_mu, 
                    u_min, 
                    u_max, 
                    u_init, 
                    U_init, 
                    u_scale, 
                    u_per_command, 
                    step_dependent_dynamics, 
                    rollout_samples, 
                    rollout_var_cost, 
                    rollout_var_discount, 
                    sample_null_action, 
                    use_priors,
                    robot_type,
                    noise_abs_cost)
        self.gym = None
        self.sim = None
        self.num_envs = num_samples
        self.robot = robot_type

        # Additional variables for the environment
        # Pushing purple blox
        self.block_index = 11
        self.block_goal = torch.tensor([3, -3], device="cuda:0")
        self.nav_goal = torch.tensor([-3, 3], device="cuda:0")

    def update_gym(self, gym, sim):
        self.gym = gym
        self.sim = sim

    def get_navigation_cost(self, r_pos):
        return torch.linalg.norm(r_pos - self.nav_goal, axis=1)

    def get_boxer_push_cost(self, r_pos):
        block_pos = torch.cat((torch.split(torch.clone(self.root_positions[:,0:2]), int(torch.clone(self.root_positions[:,0:2]).size(dim=0)/self.num_envs))),1)[self.block_index,:].reshape(self.num_envs,2)

        robot_to_block = torch.linalg.norm(r_pos - block_pos, axis = 1)
        block_to_goal = torch.linalg.norm(self.block_goal - block_pos, axis = 1)

        cost = robot_to_block + block_to_goal 

        robot_to_goal = torch.linalg.norm(r_pos - self.block_goal, axis = 1)
        align_cost = torch.zeros_like(robot_to_goal)
        align_cost[block_to_goal > robot_to_goal] = 1

        cost += align_cost
        return cost

    def get_push_cost(self, r_pos):
        block_pos = torch.cat((torch.split(torch.clone(self.root_positions[:,0:2]), int(torch.clone(self.root_positions[:,0:2]).size(dim=0)/self.num_envs))),1)[self.block_index,:].reshape(self.num_envs,2)
        return torch.linalg.norm(r_pos - block_pos, axis = 1) + torch.linalg.norm(self.block_goal - block_pos,axis = 1)

    @mppi.handle_batch_input
    def _dynamics(self, state, u, t):

        if self.robot == 'boxer':
            r = 0.08
            L = 2*0.157
            # Diff drive fk
            actions_fk = u.clone()
            actions_fk[:, 0] = (u[:, 0] / r) - ((L*u[:, 1])/(2*r))
            actions_fk[:, 1] = (u[:, 0] / r) + ((L*u[:, 1])/(2*r))
            self.gym.set_dof_velocity_target_tensor(self.sim, gymtorch.unwrap_tensor(actions_fk))
        elif self.robot == 'point_robot':
            self.gym.set_dof_velocity_target_tensor(self.sim, gymtorch.unwrap_tensor(u))
        
        self.gym.simulate(self.sim)
        self.gym.fetch_results(self.sim, True)
        actor_root_state = gymtorch.wrap_tensor(self.gym.acquire_actor_root_state_tensor(self.sim))
        self.gym.refresh_actor_root_state_tensor(self.sim)
        self.gym.refresh_dof_state_tensor(self.sim)
        self.root_positions = actor_root_state[:, 0:3]
        dof_states, _, _, _ = sim_init.acquire_states(self.gym, self.sim, print_flag=False)

        if self.robot == 'boxer':
            res_ = actor_root_state[13::14]
            res = torch.cat([res_[:, 0:2], res_[:, 7:9]], axis=1)
        elif self.robot == 'point_robot':
            res = torch.clone(dof_states).view(-1, 4)   
        return res

    @mppi.handle_batch_input
    def _running_cost(self, state, u):
        # State: for each environment, the current state containing position and velocity
        # Action: same but for control input
        
        state_pos = torch.cat((state[:, 0].unsqueeze(1), state[:, 2].unsqueeze(1)), 1)
        control_cost = torch.sum(torch.square(u),1)
        w_u = 0.01
        # Contact forces
        _net_cf = self.gym.acquire_net_contact_force_tensor(self.sim)
        net_cf = gymtorch.wrap_tensor(_net_cf)
        _net_cf = self.gym.refresh_net_contact_force_tensor(self.sim)
        # Take only forces in x,y in modulus for each environment. Avoid all collisions
        net_cf = torch.sum(torch.abs(torch.cat((net_cf[:, 0].unsqueeze(1), net_cf[:, 1].unsqueeze(1)), 1)),1)
        # The last 6 actors are allowed to collide with eachother (movabable obstacles and robot)
        coll_cost = torch.sum(net_cf.reshape([self.num_envs, int(net_cf.size(dim=0)/self.num_envs)])[:,0:11], 1)
        w_c = 100000 # Weight for collisions
        # Binary check for collisions. So far checking all collision with unmovable obstacles. Movable obstacles touching unmovable ones are considered collisions       
        coll_cost[coll_cost>0.1] = 1
        coll_cost[coll_cost<=0.1] = 0
        if self.robot == 'boxer':
            task_cost = self.get_boxer_push_cost(state[:, :2])
        elif self.robot == 'point_robot':
            task_cost = self.get_push_cost(state_pos)
            #task_cost = self.get_navigation_cost(state_pos)
        return  task_cost + w_c*coll_cost # + w_u*control_cost 