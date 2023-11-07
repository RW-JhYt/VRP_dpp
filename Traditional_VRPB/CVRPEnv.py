
from dataclasses import dataclass
import torch
import numpy as np

from CVRProblemDef import get_random_problems, augment_xy_data_by_8_fold


@dataclass
class Reset_State:
    depot_xy: torch.Tensor = None
    # shape: (batch, 1, 2)
    node_xy: torch.Tensor = None
    # shape: (batch, problem, 2)
    node_demand: torch.Tensor = None
    # shape: (batch, problem)


@dataclass
class Step_State:
    BATCH_IDX: torch.Tensor = None
    POMO_IDX: torch.Tensor = None
    # shape: (batch, pomo)
    selected_count: int = None
    load: torch.Tensor = None
    # shape: (batch, pomo)
    current_node: torch.Tensor = None
    # shape: (batch, pomo)
    ninf_mask: torch.Tensor = None
    # shape: (batch, pomo, problem+1)
    finished: torch.Tensor = None
    # shape: (batch, pomo)
    to_delivery: torch.Tensor = None
    to_pickup: torch.Tensor = None


class CVRPEnv:
    def __init__(self, **env_params):

        # Const @INIT
        ####################################
        self.env_params = env_params
        self.problem_size = env_params['problem_size']
        self.pomo_size = env_params['pomo_size']

        self.FLAG__use_saved_problems = False
        self.saved_depot_xy = None
        self.saved_node_xy = None
        self.saved_node_demand = None
        self.saved_index = None

        # Const @Load_Problem
        ####################################
        self.batch_size = None
        self.BATCH_IDX = None
        self.POMO_IDX = None
        # IDX.shape: (batch, pomo)
        self.depot_node_xy = None
        # shape: (batch, problem+1, 2)
        self.depot_node_demand = None
        # shape: (batch, problem+1)

        # Dynamic-1
        ####################################
        self.selected_count = None
        self.current_node = None
        # shape: (batch, pomo)
        self.selected_node_list = None
        # shape: (batch, pomo, 0~)

        # Dynamic-2
        ####################################
        self.at_the_depot = None
        # shape: (batch, pomo)
        self.load = None
        # shape: (batch, pomo)
        self.visited_ninf_flag = None
        # shape: (batch, pomo, problem+1)
        self.ninf_mask = None
        # shape: (batch, pomo, problem+1)
        self.finished = None
        # shape: (batch, pomo)

        self.to_delivery = None
        self.to_pickup = None

        # states to return
        ####################################
        self.reset_state = Reset_State()
        self.step_state = Step_State()

    def use_saved_problems(self, filename, device):
        self.FLAG__use_saved_problems = True

        loaded_dict = torch.load(filename, map_location=device)
        self.saved_depot_xy = loaded_dict['depot_xy']
        self.saved_node_xy = loaded_dict['node_xy']
        self.saved_node_demand = loaded_dict['node_demand']
        self.saved_index = 0

    def load_problems(self, batch_size, aug_factor=1):
        self.batch_size = batch_size

        if not self.FLAG__use_saved_problems:
            depot_xy, node_xy, node_demand = get_random_problems(batch_size, self.problem_size, self.pomo_size)
        else:
            depot_xy = self.saved_depot_xy[self.saved_index:self.saved_index+batch_size]
            node_xy = self.saved_node_xy[self.saved_index:self.saved_index+batch_size]
            node_demand = self.saved_node_demand[self.saved_index:self.saved_index+batch_size]
            self.saved_index += batch_size

        if aug_factor > 1:
            if aug_factor == 8:
                self.batch_size = self.batch_size * 8
                depot_xy = augment_xy_data_by_8_fold(depot_xy)
                node_xy = augment_xy_data_by_8_fold(node_xy)
                node_demand = node_demand.repeat(8, 1)
            else:
                raise NotImplementedError

        self.depot_node_xy = torch.cat((depot_xy, node_xy), dim=1)
        # shape: (batch, problem+1, 2)
        depot_demand = torch.zeros(size=(self.batch_size, 1))
        # shape: (batch, 1)
        self.depot_node_demand = torch.cat((depot_demand, node_demand), dim=1)
        # shape: (batch, problem+1)


        self.BATCH_IDX = torch.arange(self.batch_size)[:, None].expand(self.batch_size, self.pomo_size)
        self.POMO_IDX = torch.arange(self.pomo_size)[None, :].expand(self.batch_size, self.pomo_size)

        self.reset_state.depot_xy = depot_xy
        self.reset_state.node_xy = node_xy
        self.reset_state.node_demand = node_demand

        self.step_state.BATCH_IDX = self.BATCH_IDX
        self.step_state.POMO_IDX = self.POMO_IDX

    def reset(self):
        self.selected_count = 0
        self.current_node = None
        # shape: (batch, pomo)
        self.selected_node_list = torch.zeros((self.batch_size, self.pomo_size, 0), dtype=torch.long)
        # shape: (batch, pomo, 0~)

        self.at_the_depot = torch.ones(size=(self.batch_size, self.pomo_size), dtype=torch.bool)
        # shape: (batch, pomo)
        self.load = torch.ones(size=(self.batch_size, self.pomo_size))
        # shape: (batch, pomo)
        self.visited_ninf_flag = torch.zeros(size=(self.batch_size, self.pomo_size, self.problem_size+1))
        # shape: (batch, pomo, problem+1)
        self.ninf_mask = torch.zeros(size=(self.batch_size, self.pomo_size, self.problem_size+1))
        # shape: (batch, pomo, problem+1)
        self.finished = torch.zeros(size=(self.batch_size, self.pomo_size), dtype=torch.bool)
        # shape: (batch, pomo)


        self.to_delivery = torch.cat([torch.zeros(self.batch_size, 1, 1, dtype=torch.uint8),
                               torch.ones(self.batch_size, 1, self.pomo_size, dtype=torch.uint8),
                               torch.zeros(self.batch_size, 1, self.problem_size - self.pomo_size, dtype=torch.uint8)], dim=-1)

        self.to_pickup = torch.cat([torch.zeros(self.batch_size, 1, self.pomo_size + 1, dtype=torch.uint8),
                                 torch.ones(self.batch_size, 1, self.problem_size - self.pomo_size, dtype=torch.uint8)], dim=-1)

        # shape: (batch_size, 1, graph_size+1)


        reward = None
        done = False
        return self.reset_state, reward, done

    def pre_step(self):
        self.step_state.selected_count = self.selected_count
        self.step_state.load = self.load
        self.step_state.current_node = self.current_node
        self.step_state.ninf_mask = self.ninf_mask
        self.step_state.finished = self.finished

        self.step_state.to_delivery = self.to_delivery
        self.step_state.to_pickup = self.to_pickup

        reward = None
        done = False
        return self.step_state, reward, done

    def step(self, selected):
        # selected.shape: (batch, pomo)

        # Dynamic-1
        ####################################
        self.selected_count += 1
        self.current_node = selected
        # shape: (batch, pomo)
        self.selected_node_list = torch.cat((self.selected_node_list, self.current_node[:, :, None]), dim=2)
        # shape: (batch, pomo, 0~)

        # Dynamic-2
        ####################################
        self.at_the_depot = (selected == 0)

        demand_list = self.depot_node_demand[:, None, :].expand(self.batch_size, self.pomo_size, -1)
        # shape: (batch, pomo, problem+1)
        gathering_index = selected[:, :, None]
        # shape: (batch, pomo, 1)
        selected_demand = demand_list.gather(dim=2, index=gathering_index).squeeze(dim=2)
        # shape: (batch, pomo)


        self.load -= selected_demand


        self.load[self.at_the_depot] = 1 # refill loaded at the depot
        self.visited_ninf_flag[self.BATCH_IDX, self.POMO_IDX, selected] = float('-inf')
        # shape: (batch, pomo, problem+1)
        self.visited_ninf_flag[:, :, 0][
            ~self.at_the_depot] = 0  # depot is considered unvisited, unless you are AT the depot
        self.ninf_mask = self.visited_ninf_flag.clone()


        prev_a = []
        if self.selected_count > 2:
            for i in self.selected_node_list[0]:
                prev_a.append(int(i[-2]))

        prev_a = torch.tensor(prev_a).unsqueeze(0)


        if self.selected_count < self.pomo_size * (self.pomo_size):
            for n in range(self.pomo_size):
                if (self.pomo_size + 1) * 2 * n <= self.selected_count - 1 < (self.pomo_size + 1) * (2 * n + 1):

                    round_error_epsilon = 0.00001

                    demand_too_large = (self.load[:, :, None] + round_error_epsilon < demand_list) |\
                                       (1 - self.to_delivery).bool()


                    # shape: (batch, pomo, problem+1)
                    self.ninf_mask[demand_too_large] = float('-inf')
                    # shape: (batch, pomo, problem+1)

                    nan1 = (self.ninf_mask == float('-inf')).all(dim=2).squeeze().tolist()

                    num_batch1 = 0
                    i1 = 0
                    for n1 in nan1:
                        if len(np.array(nan1).shape) == 1:
                            if n1 is True:
                                self.ninf_mask[0, i1][selected[0, i1]] = 0
                                self.load[0, i1] += selected_demand[0, i1]
                                i1 = i1 + 1
                            else:
                                i1 = i1 + 1
                        else:
                            i1 = 0
                            for batch_non1 in n1:
                                if batch_non1 is True:
                                    self.ninf_mask[num_batch1, i1][selected[num_batch1, i1]] = 0
                                    self.load[num_batch1, i1] += selected_demand[num_batch1, i1]
                                    i1 = i1 + 1
                                else:
                                    i1 = i1 + 1
                            num_batch1 += 1


                    break


                if (self.pomo_size + 1) * (2 * n + 1) <= self.selected_count - 1 < (self.pomo_size + 1) * (2 * n + 2):
                    if (selected == prev_a) is not None:
                        nan3 = (self.ninf_mask == float('-inf')).all(dim=2).squeeze().tolist()

                        num_batch3 = 0
                        i3 = 0
                        for n3 in nan3:
                            if len(np.array(nan3).shape) == 1:
                                if n3 is True:
                                    self.load[0, i3] -= selected_demand[0, i3]
                                    i3 = i3 + 1
                                else:
                                    i3 = i3 + 1
                            else:
                                i3 = 0
                                for batch_non3 in n3:
                                    if batch_non3 is True:
                                        self.load[num_batch3, i3] -= selected_demand[num_batch3, i3]
                                        i3 = i3 + 1
                                    else:
                                        i3 = i3 + 1
                                num_batch3 += 1




                    round_error_epsilon = 0.00001

                    demand_too_small = (self.load[:, :, None] -  1 + round_error_epsilon > demand_list)\
                                       | (1 - self.to_pickup).bool()


                    self.ninf_mask[demand_too_small] = float('-inf')
                    # shape: (batch, pomo, problem+1)

                    nan2 = (self.ninf_mask == float('-inf')).all(dim=2).squeeze().tolist()


                    num_batch2 = 0
                    i2 = 0
                    for n2 in nan2:
                        if len(np.array(nan2).shape) == 1:
                            if n2 is True:
                                self.ninf_mask[0, i2][0] = 0
                                i2 = i2 + 1
                            else:
                                i2 = i2 + 1
                        else:
                            i2 = 0
                            for batch_non2 in n2:
                                if batch_non2 is True:
                                    self.ninf_mask[num_batch2, i2][0] = 0
                                    i2 = i2 + 1
                                else:
                                    i2 = i2 + 1
                            num_batch2 += 1


                    break



        else:
            self.ninf_mask = self.visited_ninf_flag.clone()
            demand_too_large = self.load[:, :, None] - demand_list > 2
            # shape: (batch, pomo, problem+1)
            self.ninf_mask[demand_too_large] = float('-inf')
            # shape: (batch, pomo, problem+1)



        newly_finished = (self.visited_ninf_flag == float('-inf')).all(dim=2)
        # shape: (batch, pomo)
        self.finished = self.finished + newly_finished
        # shape: (batch, pomo)

        # do not mask depot for finished episode.
        self.ninf_mask[:, :, 0][self.finished] = 0

        self.step_state.selected_count = self.selected_count
        self.step_state.load = self.load
        self.step_state.current_node = self.current_node
        self.step_state.ninf_mask = self.ninf_mask
        self.step_state.finished = self.finished

        # returning values
        done = self.finished.all()
        if done:
            reward = -self._get_travel_distance()  # note the minus sign!
        else:
            reward = None

        return self.step_state, reward, done

    def _get_travel_distance(self):
        gathering_index = self.selected_node_list[:, :, :, None].expand(-1, -1, -1, 2)
        # shape: (batch, pomo, selected_list_length, 2)
        all_xy = self.depot_node_xy[:, None, :, :].expand(-1, self.pomo_size, -1, -1)
        # shape: (batch, pomo, problem+1, 2)

        ordered_seq = all_xy.gather(dim=2, index=gathering_index)
        # shape: (batch, pomo, selected_list_length, 2)

        rolled_seq = ordered_seq.roll(dims=2, shifts=-1)
        segment_lengths = ((ordered_seq-rolled_seq)**2).sum(3).sqrt()
        # shape: (batch, pomo, selected_list_length)

        travel_distances = segment_lengths.sum(2)
        # shape: (batch, pomo)
        return travel_distances

