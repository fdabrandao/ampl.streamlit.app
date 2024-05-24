import streamlit as st
from amplpy import AMPL
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from . import examples, stnutils
import math
import json
import os


class BatchProcessOptimizer:
    def __init__(self, stn):
        unit_tasks_pd = pd.DataFrame.from_dict(stn["UNIT_TASKS"], orient="index")
        states_df = pd.DataFrame.from_dict(stn["STATES"], orient="index")

        # 1. Characterization of tasks
        self.STATES = stn["STATES"]
        self.ST_ARCS = stn["ST_ARCS"]
        self.TS_ARCS = stn["TS_ARCS"]
        self.UNIT_TASKS = stn["UNIT_TASKS"]
        self.TIME = stn["TIME"]
        self.H = max(self.TIME)
        # set of tasks
        self.TASKS = set([i for (j, i) in self.UNIT_TASKS])

        # S[i] input set of states which feed task i
        self.S = {i: set() for i in self.TASKS}
        for s, i in self.ST_ARCS:
            self.S[i].add(s)

        # S_[i] output set of states fed by task i
        self.S_ = {i: set() for i in self.TASKS}
        for i, s in self.TS_ARCS:
            self.S_[i].add(s)

        # rho[(i,s)] input fraction of task i from state s
        self.rho = {(i, s): self.ST_ARCS[(s, i)]["rho"] for (s, i) in self.ST_ARCS}

        # rho_[(i,s)] output fraction of task i to state s
        self.rho_ = {(i, s): self.TS_ARCS[(i, s)]["rho"] for (i, s) in self.TS_ARCS}

        # P[(i,s)] time for task i output to state s
        self.P = {(i, s): self.TS_ARCS[(i, s)]["dur"] for (i, s) in self.TS_ARCS}

        # p[i] completion time for task i
        self.p = {i: max([self.P[(i, s)] for s in self.S_[i]]) for i in self.TASKS}

        # K[i] set of units capable of task i
        self.K = {i: set() for i in self.TASKS}
        for j, i in self.UNIT_TASKS:
            self.K[i].add(j)

        # 2. Characterization of states
        # T[s] set of tasks receiving material from state s
        self.T = {s: set() for s in self.STATES}
        for s, i in self.ST_ARCS:
            self.T[s].add(i)

        # set of tasks producing material for state s
        self.T_ = {s: set() for s in self.STATES}
        for i, s in self.TS_ARCS:
            self.T_[s].add(i)

        # C[s] storage capacity for state s
        self.C = {s: self.STATES[s]["capacity"] for s in self.STATES}

        # 3. Characterization of units
        self.UNITS = list(sorted(set([j for (j, i) in self.UNIT_TASKS])))

        # I[j] set of tasks performed with unit j
        self.I = {j: set() for j in self.UNITS}
        for j, i in self.UNIT_TASKS:
            self.I[j].add(i)

        # Bmax[(i,j)] maximum capacity of unit j for task i
        self.Bmax = {
            (i, j): self.UNIT_TASKS[(j, i)]["Bmax"] for (j, i) in self.UNIT_TASKS
        }

        # Bmin[(i,j)] minimum capacity of unit j for task i
        self.Bmin = {
            (i, j): self.UNIT_TASKS[(j, i)]["Bmin"] for (j, i) in self.UNIT_TASKS
        }

        ampl = AMPL()
        ampl.cd(os.path.dirname(__file__))
        ampl.read("batch_process.mod")
        self.TIME = np.array(self.TIME)
        ampl.set["TIME"] = self.TIME
        ampl.set["TASKS"] = self.TASKS
        ampl.set["UNITS"] = self.UNITS
        ampl.set["STATES"] = self.STATES.keys()
        ampl.set["I"] = self.I
        ampl.set["K"] = self.K
        ampl.set["T_In"] = self.T_
        ampl.set["T_Out"] = self.T
        ampl.set["S_In"] = self.S
        ampl.set["S_Out"] = self.S_
        ampl.param["H"] = self.H
        ampl.param["price"] = states_df[["price"]]
        ampl.param["initial"] = states_df[["initial"]]
        ampl.param["P"] = self.P
        ampl.param["p"] = self.p
        ampl.param["C"] = self.C
        ampl.param["rho_in"] = self.rho
        ampl.param["rho_out"] = self.rho_
        ampl.param["Bmin"] = unit_tasks_pd[["Bmin"]]
        ampl.param["Bmax"] = unit_tasks_pd[["Bmax"]]
        ampl.param["Cost"] = unit_tasks_pd[["Cost"]]
        ampl.param["vCost"] = unit_tasks_pd[["vCost"]]
        ampl.param["Tclean"] = unit_tasks_pd[["Tclean"]]
        self.ampl = ampl

    def solve(self, solver):
        ampl = self.ampl
        ampl.option["highs_options"] = "outlev=1 timelim=15"
        ampl.option["gurobi_options"] = "outlev=1 timelim=15"
        ampl.option["cplex_options"] = "outlev=1 timelim=15"
        # Write json file for debugging
        # open(os.path.join(os.path.dirname(__file__), "input.json"), "w").write(
        #     json.dumps({"data": ampl.export_data()})
        # )
        output = ampl.solve(solver=solver, return_output=True)
        sol = self.ampl.get_solution(flat=False, zeros=True)
        self.solution = {
            "total_value": ampl.get_value("TotalValue"),
            "total_cost": ampl.get_value("TotalCost"),
            "total_profit": ampl.get_value("Total_Profit"),
            "W": sol["W"],
            "B": sol["B"],
            "S": sol["S"],
            "Q": sol["Q"],
        }
        return output

    def solution_analysis(self):
        solution = self.solution
        st.write(self.solution["W"])
        total_value = self.solution["total_value"]
        total_cost = self.solution["total_cost"]

        st.write("## Analysis")
        st.write(
            f"""
            ### Profitability
            - Value of State Inventories = {total_value:12.2f}
            - Cost of Unit Assignments = {total_cost:12.2f}
            - Net Objective = {total_value - total_cost:12.2f}
            """
        )

        st.write("### Unit assignment")

        UnitAssignment = pd.DataFrame(
            {j: [None for t in self.TIME] for j in self.UNITS}, index=self.TIME
        )

        for t in self.TIME:
            for j in self.UNITS:
                for i in self.I[j]:
                    for s in self.S_[i]:
                        if t - self.p[i] >= 0:
                            if (
                                solution["W"][
                                    i, j, max(self.TIME[self.TIME <= t - self.p[i]])
                                ]
                                > 0
                            ):
                                UnitAssignment.loc[t, j] = None
                for i in self.I[j]:
                    if solution["W"][i, j, t] > 0:
                        UnitAssignment.loc[t, j] = (i, solution["B"][i, j, t])

        UnitAssignment.index.names = ["Time"]

        st.write(UnitAssignment.applymap(lambda value: f"{value}"))

        st.write("### State inventories")

        plt.figure(figsize=(10, 6))
        for s, idx in zip(self.STATES.keys(), range(0, len(self.STATES.keys()))):
            plt.subplot(math.ceil(len(self.STATES.keys()) / 3), 3, idx + 1)
            tlast, ylast = 0, self.STATES[s]["initial"]
            for t, y in zip(list(self.TIME), [solution["S"][s, t] for t in self.TIME]):
                plt.plot([tlast, t, t], [ylast, ylast, y], "b")
                # plt.plot([tlast,t],[ylast,y],'b.',ms=10)
                tlast, ylast = t, y
            plt.ylim(0, 1.1 * self.C[s])
            plt.plot([0, self.H], [self.C[s], self.C[s]], "r--")
            plt.title(s)
        # plt.tight_layout()
        st.pyplot(plt)

        st.write("### Unit batch inventories")

        df = pd.DataFrame(
            [[solution["Q"][j, t] for j in self.UNITS] for t in self.TIME],
            columns=self.UNITS,
            index=self.TIME,
        )
        df.index.names = ["Time"]
        st.write(df)

        st.write("### Gannt chart")

        plt.figure(figsize=(12, 6))

        gap = self.H / 500
        idx = 1
        lbls = []
        ticks = []
        for j in sorted(self.UNITS):
            idx -= 1
            for i in sorted(self.I[j]):
                idx -= 1
                ticks.append(idx)
                lbls.append("{0:s} -> {1:s}".format(j, i))
                plt.plot([0, self.H], [idx, idx], lw=20, alpha=0.3, color="y")
                for t in self.TIME:
                    if solution["W"][i, j, t] > 0:
                        plt.plot(
                            [t + gap, t + self.p[i] - gap],
                            [idx, idx],
                            "b",
                            lw=20,
                            solid_capstyle="butt",
                        )
                        txt = "{0:.2f}".format(solution["B"][i, j, t])
                        plt.text(
                            t + self.p[i] / 2,
                            idx,
                            txt,
                            color="white",
                            weight="bold",
                            ha="center",
                            va="center",
                        )
        plt.xlim(0, self.H)
        plt.gca().set_yticks(ticks)
        plt.gca().set_yticklabels(lbls)
        st.pyplot(plt)

        st.write("### Trace of events and states")

        event_report = []

        def report(*args):
            event_report.append(" ".join(map(str, args)))

        sep = "\n--------------------------------------------------------------------------------------------\n"
        report(sep)
        report("Starting Conditions")
        report("    Initial Inventories:")
        for s in self.STATES.keys():
            report("        {0:10s}  {1:6.1f} kg".format(s, self.STATES[s]["initial"]))

        units = {j: {"assignment": "None", "t": 0} for j in self.UNITS}

        for t in self.TIME:
            report(sep)
            report("Time =", t, "hr")
            report("    Instructions:")
            for j in self.UNITS:
                units[j]["t"] += 1
                # transfer from unit to states
                for i in self.I[j]:
                    for s in self.S_[i]:
                        if t - self.P[(i, s)] >= 0:
                            amt = (
                                self.rho_[(i, s)]
                                * solution["B"][
                                    i,
                                    j,
                                    max(self.TIME[self.TIME <= t - self.P[(i, s)]]),
                                ]
                            )
                            if amt > 0:
                                report("        Transfer", amt, "kg from", j, "to", s)
            for j in self.UNITS:
                # release units from tasks
                for i in self.I[j]:
                    if t - self.p[i] >= 0:
                        if (
                            solution["W"][
                                i, j, max(self.TIME[self.TIME <= t - self.p[i]])
                            ]
                            > 0
                        ):
                            report("        Release", j, "from", i)
                            units[j]["assignment"] = "None"
                            units[j]["t"] = 0
                # assign units to tasks
                for i in self.I[j]:
                    if solution["W"][i, j, t] > 0:
                        report(
                            "        Assign",
                            j,
                            "with capacity",
                            self.Bmax[(i, j)],
                            "kg to task",
                            i,
                            "for",
                            self.p[i],
                            "hours",
                        )
                        units[j]["assignment"] = i
                        units[j]["t"] = 1
                # transfer from states to starting tasks
                for i in self.I[j]:
                    for s in self.S[i]:
                        amt = self.rho[(i, s)] * solution["B"][i, j, t]
                        if amt > 0:
                            report("        Transfer", amt, "kg from", s, "to", j)
            report("\n    Inventories are now:")
            for s in self.STATES.keys():
                report("        {0:10s}  {1:6.1f} kg".format(s, solution["S"][s, t]))
            report("\n    Unit Assignments are now:")
            for j in self.UNITS:
                if units[j]["assignment"] != "None":
                    fmt = "        {0:s} performs the {1:s} task with a {2:.2f} kg batch for hour {3:f} of {4:f}"
                    i = units[j]["assignment"]
                    report(
                        fmt.format(j, i, solution["Q"][j, t], units[j]["t"], self.p[i])
                    )

        report(sep)
        report("Final Conditions")
        report("    Final Inventories:")
        for s in self.STATES.keys():
            report("        {0:10s}  {1:6.1f} kg".format(s, solution["S"][s, self.H]))

        event_report = "\n".join(event_report)
        with st.expander("Click to expand event report"):
            st.write(
                f"""
                ```
                {event_report}
                ```
                """
            )


def main():
    st.markdown(
        r"""
    # ⚙️ Scheduling Multipurpose Batch Processes using State-Task Networks in Python

    The State-Task Network (STN) is an approach to modeling multipurpose batch process for the purpose of short term scheduling. It was first developed by Kondili, et al., in 1993, and subsequently developed and extended by others.
    
    Learn more with our notebooks on Google Colab: [Batch Process Optimization Notebooks](https://ampl.com/colab/tags/batch-processes.html)

    ## Example (Kondili, et al., 1993)

    A state-task network is a graphical representation of the activities in a multi-product batch process. The representation includes the minimum details needed for short term scheduling of batch operations.

    A well-studied example due to Kondili (1993) is shown below. Other examples are available in the references cited above.

    ![Kondili_1993.png](https://github.com/jckantor/ND-Pyomo-Cookbook/blob/master/notebooks/figures/Kondili_1993.png?raw=1)

    Each circular node in the diagram designates material in a particular state.  The materials are generally held in suitable vessels with a known capacity. The relevant information for each state is the initial inventory, storage capacity, and the unit price of the material in each state. The price of materials in intermediate states may be assigned penalties in order to minimize the amount of work in progress.

    The rectangular nodes denote process tasks. When scheduled for execution, each task is assigned an appropriate piece of equipment, and assigned a batch of material according to the incoming arcs. Each incoming arc begins at a state where the associated label indicates the mass fraction of the batch coming from that particular state. Outgoing arcs indicate the disposition of the batch to product states. The outgoing are labels indicate the fraction of the batch assigned to each product state, and the time necessary to produce that product.

    Not shown in the diagram is the process equipment used to execute the tasks. A separate list of process units is available, each characterized by a capacity and list of tasks which can be performed in that unit.
    
    ## Encoding the STN data

    The basic data structure specifies the states, tasks, and units comprising a state-task network. The intention is for all relevant problem data to be contained in the following tables.
    """
    )

    # Create a select box widget on the sidebar
    selected_stn = st.selectbox("Example STN networks 👇", ["Kondili", "Hydrolubes"])

    if selected_stn == "Kondili":
        H = examples.Kondili_H
        STN = examples.Kondili_STN
    elif selected_stn == "Hydrolubes":
        H = examples.Hydrolubes_H
        STN = examples.Hydrolubes_STN
    else:
        st.error("Invalid selection.")
        st.stop()

    H = st.slider("Time horizon 👇", max(1, H - 10), H + 10, H)

    st.write("States 👇")
    states_df = pd.DataFrame.from_dict(STN["STATES"], orient="index")
    states_df.index.names = ["state"]
    states_df = st.data_editor(states_df, num_rows="dynamic")

    st.write("State-to-task arcs indexed by (state, task) 👇")
    st_arcs_df = pd.DataFrame.from_dict(STN["ST_ARCS"], orient="index")
    st_arcs_df.index.names = ["state", "task"]
    st_arcs_df = st.data_editor(
        st_arcs_df.reset_index(), hide_index=True, num_rows="dynamic"
    )
    st_arcs_df.set_index(["state", "task"], inplace=True)

    st.write("Task-to-state arcs indexed by (task, state) 👇")
    ts_arcs_df = pd.DataFrame.from_dict(STN["TS_ARCS"], orient="index")
    ts_arcs_df.index.names = ["task", "state"]
    ts_arcs_df = st.data_editor(
        ts_arcs_df.reset_index(), hide_index=True, num_rows="dynamic"
    )
    ts_arcs_df.set_index(["task", "state"], inplace=True)

    st.write("Unit data indexed by (unit, task) 👇")
    unit_tasks_df = pd.DataFrame.from_dict(STN["UNIT_TASKS"], orient="index")
    unit_tasks_df.index.names = ["unit", "task"]
    unit_tasks_df = st.data_editor(
        unit_tasks_df.reset_index(), hide_index=True, num_rows="dynamic"
    )
    unit_tasks_df.set_index(["unit", "task"], inplace=True)

    STN = {
        "TIME": list(range(0, H + 1)),
        "STATES": states_df.to_dict(orient="index"),
        "ST_ARCS": st_arcs_df.to_dict(orient="index"),
        "TS_ARCS": ts_arcs_df.to_dict(orient="index"),
        "UNIT_TASKS": unit_tasks_df.to_dict(orient="index"),
    }

    # Draw graph
    st.write("## STN Graph")
    full_stn, full_graph = stnutils.build_graph(STN, verbose=False)
    stnutils.draw_graph(full_stn, full_graph, with_labels=True, verbose=False)

    st.write("## Let's optimize!")
    solver = st.selectbox("Solver to use 👇", ["gurobi", "highs", "cplex"])
    if solver == "cplex":
        solver = "cplexmp"

    # Load instance
    opt = BatchProcessOptimizer(full_stn)
    output = opt.solve(solver)
    st.write(
        f"""
        - Solve result: {opt.ampl.solve_result}
        - Solve time: {opt.ampl.get_value("_total_solve_time"):.2f}s
        """
    )

    with st.expander("Click to expand solve process output"):
        # Display the solve process output
        st.write("### Solve process output")
        st.write(f"```\n{output}\n```")

    if opt.ampl.solve_result in ["solved", "limit"]:
        opt.solution_analysis()

    st.markdown(
        r"""
        ## References

        - [Original notebook from the ND Pyomo Cookbook by Jeffrey C. Kantor](https://jckantor.github.io/ND-Pyomo-Cookbook/notebooks/04.05-Scheduling-Multipurpose-Batch-Processes-using-State-Task_Networks.html)
        
        - Floudas, C. A., & Lin, X. (2005). Mixed integer linear programming in process scheduling: Modeling, algorithms, and applications. Annals of Operations Research, 139(1), 131-162.

        - Harjunkoski, I., Maravelias, C. T., Bongers, P., Castro, P. M., Engell, S., Grossmann, I. E., ... & Wassick, J. (2014). Scope for industrial applications of production scheduling models and solution methods. Computers & Chemical Engineering, 62, 161-193.

        - Kondili, E., Pantelides, C. C., & Sargent, R. W. H. (1993). A general algorithm for short-term scheduling of batch operations—I. MILP formulation. Computers & Chemical Engineering, 17(2), 211-227.

        - Méndez, C. A., Cerdá, J., Grossmann, I. E., Harjunkoski, I., & Fahl, M. (2006). State-of-the-art review of optimization methods for short-term scheduling of batch processes. Computers & Chemical Engineering, 30(6), 913-946.

        - Shah, N., Pantelides, C. C., & Sargent, R. W. H. (1993). A general algorithm for short-term scheduling of batch operations—II. Computational issues. Computers & Chemical Engineering, 17(2), 229-244.

        - Wassick, J. M., & Ferrio, J. (2011). Extending the resource task network for industrial applications. Computers & chemical engineering, 35(10), 2124-2140.
        """
    )

    st.markdown(
        """
        #### [[App Source Code on GitHub](https://github.com/fdabrandao/amplopt.streamlit.app/tree/master/apps/batch_process)] [[Google Colab Notebook](https://ampl.com/colab/tags/batch-processes.html)]
        """
    )
