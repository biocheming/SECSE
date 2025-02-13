#!/usr/bin/env python  
# -*- coding:utf-8 _*-
""" 
@author: Lu Chong
@file: grow_processes.py
@time: 2021/11/17/13:49
"""
import csv
import shutil
import os
import subprocess
import pandas as pd
import rdkit
import configparser
from evaluate.glide_docking import dock_by_glide
from growing.mutation.mutation import mutation_df
from scoring.ranking import Ranking
from scoring.diversity_score import clustering
from scoring.docking_score_prediction import prepare_files
from evaluate.vina_docking import dock_by_py_vina
import time

rdkit.RDLogger.DisableLog("rdApp.*")


class Grow(object):
    def __init__(self, generation, mols_smi, workdir, num_per_gen, docking_program,
                 receptor, start_gen, dl_mode, config_path,
                 cpu_num=0, x=0, y=0, z=0, box_size_x=0, box_size_y=0, box_size_z=0):
        self.mols_smi = mols_smi
        self.total_generation = int(generation)
        self.workdir = workdir
        self.num_per_gen = num_per_gen
        self.cpu_num = cpu_num

        self.target = receptor
        self.x = x
        self.y = y
        self.z = z
        self.box_size_x = box_size_x
        self.box_size_y = box_size_y
        self.box_size_z = box_size_z

        self.gen = start_gen  # generation num for now
        self.docking_program = docking_program.lower()
        self.dl_mode = dl_mode

        self.config_path = config_path

        self.lig_sdf = None
        self.winner_df = None
        self.winner_path = None
        self._generation_dir = None
        self._filter_df = None
        self._dock_df = None
        self._sampled_df = None
        self.workdir_now = None

    def docking_sh(self, step):
        start = time.time()
        os.makedirs(self.workdir_now, exist_ok=True)

        if "vina" in self.docking_program:
            self.docking_vina(step)
        elif "glide" in self.docking_program:
            self.docking_glide(step)

        # ranking and find top fragments
        self.lig_sdf = os.path.join(self.workdir_now, "docking_outputs_with_score.sdf")
        end = time.time()
        print("Docking time cost: {} min.".format(round((end - start) / 60, 2)))

    def docking_vina(self, step):
        print("Step {}: Docking with Autodock Vina ...".format(step))
        dock_by_py_vina(self.workdir_now, self.mols_smi, self.target, self.cpu_num, self.x, self.y, self.z,
                        self.box_size_x, self.box_size_y, self.box_size_z)

    def docking_glide(self, step):
        print("Step {}: Docking with Glide ...".format(step))
        # set different docking precision for different generation
        if self.gen < 1:
            dock_mode = "SP"
        else:
            dock_mode = "HTVS"
        dock_by_glide(self.workdir_now, self.mols_smi, self.target, self.gen, dock_mode, self.cpu_num)

    def ranking_docked_mols(self, step=2):
        print("Step {}: Ranking docked molecules...".format(str(step)))
        ranking = Ranking(sdf=self.lig_sdf, gen=self.gen, config_file=self.config_path)

        ranking.docked_df.to_csv(
            os.path.join(self.workdir, "generation_" + str(self.gen), "docked_gen_" + str(self.gen) + ".csv"),
            index=False)
        ranking.tournament_selection()
        # merge mols whose evaluate score below the cutoff
        ranking.mols_score_below_cutoff()
        self.winner_df = ranking.final_df
        # generate smi file
        self.winner_path = os.path.join(self.workdir, "generation_" + str(self.gen),
                                        "best_fragment_gen_" + str(self.gen) + ".smi")
        self.winner_df["id_gen_" + str(self.gen)] = self.winner_df["id_gen_" + str(self.gen)].apply(
            lambda x: x.split("\t")[0])
        self.winner_df[["smiles_gen_" + str(self.gen), "id_gen_" + str(self.gen)]].to_csv(self.winner_path, sep="\t",
                                                                                          index=False,
                                                                                          quoting=csv.QUOTE_NONE)

    def dl_pre(self, step):
        print("Step {}.1: Building deep learning models...".format(str(step)))

        train, pre = prepare_files(self.gen, self.workdir, self.dl_mode)
        dl_shell = os.path.join(os.getenv("SECSE"), "scoring", "chemprop_pre.sh")
        config = configparser.ConfigParser()
        config.read(self.config_path)

        dl_select_num = config.get("deep learning", "dl_per_gen")
        dl_cmd = " ".join([dl_shell, self.workdir, train, pre, str(self.gen), dl_select_num, "22"])
        print(dl_cmd)
        subprocess.check_output(dl_cmd, shell=True, stderr=subprocess.STDOUT)
        # docking top predicted compounds
        self.workdir_now = os.path.join(self.workdir, "generation_{}_pre".format(self.gen))
        self.mols_smi = os.path.join(self.workdir_now, "mols_for_docking_pred.smi")
        self.docking_sh(str(step) + ".2")

        # merge results to the current generation if prediction per generation
        if self.dl_mode == 1:
            self.lig_sdf = os.path.join(self.workdir, "generation_{}".format(self.gen),
                                        "docking_outputs_with_score.sdf")
            merge_cmd = ["cat", os.path.join(self.workdir_now, "docking_outputs_with_score.sdf"), ">>", self.lig_sdf]
            subprocess.check_output(" ".join(merge_cmd), shell=True, stderr=subprocess.STDOUT)
            self.workdir_now = os.path.join(self.workdir, "generation_{}".format(self.gen))

    def grow(self):
        print("\n{}\nInput fragment file: {}".format("*" * 66, self.mols_smi))
        print("Target grid file: {}".format(self.target))
        print("Workdir: {}\n".format(self.workdir))
        # generation 0 : 1.evaluate; 2.ranking
        self.workdir_now = os.path.join(self.workdir, "generation_" + str(self.gen))
        step = 1
        self.docking_sh(step)
        step += 1
        self.ranking_docked_mols(step)

        # next generations: 1.copy best mols from last generation as seed; 2.mutation; 3.filter; 4. sampling;
        #                   5.clustering; 6.evaluate; 7.ranking
        for g in range(1, self.total_generation + 1):
            self.gen += 1
            print("\n", "*" * 50, "\nGeneration ", str(self.gen), "...")
            self.workdir_now = os.path.join(self.workdir, "generation_" + str(self.gen))
            if os.path.exists(self.workdir_now):
                shutil.rmtree(self.workdir_now)
            os.makedirs(self.workdir_now, exist_ok=True)
            self.winner_df.to_csv(os.path.join(self.workdir_now, "seed_fragments.smi"), sep="\t", index=False,
                                  quoting=csv.QUOTE_NONE)
            # mutation
            print("Step 1: Mutation")

            self._generation_dir = os.path.join(self.workdir_now, "generation_split_by_seed")
            self.winner_df = self.winner_df.reset_index(drop=True)
            header = mutation_df(self.winner_df, self.workdir, self.cpu_num, self.gen)
            generation_path = os.path.join(self.workdir_now, "generation")

            cmd_cat = "cat {} > {}".format(os.path.join(self.workdir_now, "mutation.csv"),
                                           generation_path + ".raw")
            subprocess.check_output(cmd_cat, shell=True, stderr=subprocess.STDOUT)
            cmd_dedup = "awk -F',' '!seen[$(NF-4)]++' " + generation_path + ".raw > " + generation_path + ".csv"
            subprocess.check_output(cmd_dedup, shell=True, stderr=subprocess.STDOUT)
            if not os.path.exists(self._generation_dir):
                os.mkdir(self._generation_dir)
            cmd_split = "awk -F, '{print>\"" + self._generation_dir + "/\"$2\".csv\"}' " + generation_path + ".csv"
            subprocess.check_output(cmd_split, shell=True, stderr=subprocess.STDOUT)
            # filter
            print("Step 2: Filtering all mutated mols")
            time1 = time.time()
            cmd_filter = ["sh", os.path.join(os.getenv("SECSE"), "growing", "filter_parallel.sh"), self.workdir_now,
                          str(self.gen), self.config_path, str(self.cpu_num)]
            cmd_filter = " ".join(cmd_filter)
            print(cmd_filter)
            subprocess.check_output(cmd_filter, shell=True, stderr=subprocess.STDOUT)
            time2 = time.time()
            print("Filter runtime: {:.2f} min.".format((time2 - time1) / 60))

            # do not sample or clustering if generated molecules less than wanted size
            self._filter_df = pd.read_csv(os.path.join(self.workdir_now, "filter.csv"), header=None)
            self._filter_df.columns = header + ["flag"]
            self._filter_df["type"] = self._filter_df["reaction_id_gen_" + str(self.gen)].apply(
                lambda x: "-".join(x.split("-")[:2]))
            self._filter_df.to_csv(os.path.join(self.workdir_now, "filter.csv"), index=False)
            if self._filter_df.shape[0] <= self.num_per_gen:
                self._dock_df = self._filter_df
                self._dock_df.to_csv(os.path.join(self.workdir_now, "sampled.csv"), index=False)
            else:
                # sampling
                print("Step 3: Sampling")

                if "G-002" in list(self._filter_df["type"]):
                    # control ratio of G-002 mutation
                    spacer_df = self._filter_df[self._filter_df["type"] == "G-002"]

                    common_df = self._filter_df.drop(spacer_df.index, axis=0)
                    # control ratio of ring with spacer based on different stage
                    if self.gen <= 3:
                        spacer_ratio = 0.3
                    elif self.gen <= 7:
                        spacer_ratio = 0.1
                    else:
                        spacer_ratio = 0.01
                    sample_size = min(self._filter_df.shape[0], 500000)

                    spacer_df = spacer_df.sample(min(int(sample_size * spacer_ratio), spacer_df.shape[0]),
                                                 replace=False,
                                                 weights="priority_gen_" + str(self.gen))

                    common_df = common_df.sample(min(int(sample_size * (1 - spacer_ratio)), common_df.shape[0]),
                                                 replace=False,
                                                 weights="priority_gen_" + str(self.gen))
                    self._sampled_df = pd.concat([spacer_df, common_df], axis=0)
                    self._sampled_df.to_csv(os.path.join(self.workdir_now, "sampled.csv"), index=False)
                else:
                    print("No cmpds generated from ring with spacer in the generation!")
                    self._sampled_df = self._filter_df.sample(min(self._filter_df.shape[0], 500000),
                                                              replace=False,
                                                              weights="priority_gen_" + str(self.gen))
                    self._sampled_df.to_csv(os.path.join(self.workdir_now, "sampled.csv"), index=False)

                print("Step 4: Clustering")
                # clustering
                num_clusters = int(self.num_per_gen / 5)
                self._sampled_df = clustering(self._sampled_df, "smiles_gen_" + str(self.gen), self.gen, self.cpu_num,
                                              num_clusters)

                # sample enough mol
                self._dock_df = self._sampled_df.sort_values("cluster_center_dis_gen_" + str(self.gen)).groupby(
                    "cluster_center_gen_" + str(self.gen)).head(int(self.num_per_gen / num_clusters) + 1)

            # write file for evaluate
            self.mols_smi = os.path.join(self.workdir_now, "mols_for_docking.smi")
            self._dock_df[["smiles_gen_" + str(self.gen), "id_gen_" + str(self.gen)]].to_csv(self.mols_smi, index=False,
                                                                                             header=False, sep="\t")
            # evaluate
            step = 5
            self.docking_sh(step)
            # deep learning model
            if self.dl_mode == 1:
                step += 1
                self.dl_pre(step)
            # ranking
            step += 1
            self.ranking_docked_mols(step)

        if self.dl_mode == 2:
            step += 1
            self.dl_pre(step)
