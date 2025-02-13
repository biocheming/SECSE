#!/usr/bin/env python  
# -*- coding:utf-8 _*-
""" 
@author: Lu Chong
@file: vina_docking.py
@time: 2021/9/6/11:22
"""
import argparse
import subprocess
import os, sys
import shutil
from rdkit import Chem
from rdkit.Chem import AllChem

sys.path.append(os.getenv("SECSE"))

VINA_SHELL = os.path.join(os.getenv("SECSE"), "evaluate", "ligprep_vina_parallel.sh")


def dock_by_py_vina(workdir, smi, receptor, cpu_num, x, y, z, box_size_x=20, box_size_y=20, box_size_z=20):
    cmd = " ".join(
        list(map(str, [VINA_SHELL, workdir, smi, receptor, x, y, z, box_size_x, box_size_y, box_size_z, cpu_num])))
    print(cmd)
    subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
    # modify output sdf
    check_mols(workdir)
    out_sdf = os.path.join(workdir, "docking_outputs_with_score.sdf")
    cmd_cat = "find {} -name \"*sdf\" | xargs -n 100 cat > {}".format(os.path.join(workdir, "sdf_files"), out_sdf)
    print(cmd_cat)
    subprocess.check_output(cmd_cat, shell=True, stderr=subprocess.STDOUT)
    # remove temporary files
    shutil.rmtree(os.path.join(workdir, "pdb_files"))
    shutil.rmtree(os.path.join(workdir, "ligands_for_vina"))
    shutil.rmtree(os.path.join(workdir, "vina_poses"))
    shutil.rmtree(os.path.join(workdir, "docking_split"))


def check_mols(workdir):
    files = os.listdir(os.path.join(workdir, "pdb_files"))
    for i in files:
        raw_id = i.rsplit("-dp", 1)[0]
        pdb_path = os.path.join(workdir, "pdb_files", i)
        sdf_path = os.path.join(workdir, "sdf_files", i.replace("pdb", "sdf"))
        raw_mol = Chem.SDMolSupplier(os.path.join(workdir, "ligands_for_vina", raw_id + ".sdf"))[0]
        mol = AllChem.MolFromPDBFile(pdb_path, removeHs=True)
        if mol:
            try:
                new = AllChem.AssignBondOrdersFromTemplate(raw_mol, mol)
            except ValueError:
                print("Failed check: ", i)
                continue
            Chem.MolToMolFile(new, sdf_path)
            with open(pdb_path, "r") as pdb:
                for line in pdb.readlines():
                    if line.startswith("REMARK VINA RESULT"):
                        score = line.split(":")[1][:10].replace(" ", "")
                        with open(sdf_path, "a") as sdf:
                            newline = "\n".join(["> <docking score>", score, "\n$$$$\n"])
                            sdf.write(newline)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Run AutoDock Vina for SMILES Format.")
    parser.add_argument("workdir", help="Workdir")
    parser.add_argument("mols_smi", help="Seed fragments")
    parser.add_argument("receptor", help="Target PDBQT")

    parser.add_argument("x", help="Docking box x", type=float)
    parser.add_argument("y", help="Docking box y", type=float)
    parser.add_argument("z", help="Docking box z", type=float)

    parser.add_argument("box_size_x", help="Docking box size x, default 20", type=float, default=20)
    parser.add_argument("box_size_y", help="Docking box size y, default 20", type=float, default=20)
    parser.add_argument("box_size_z", help="Docking box size z, default 20", type=float, default=20)

    args = parser.parse_args()
    dock_by_py_vina(args.workdir, args.mols_smi, args.receptor, args.x, args.y, args.z,
                    args.box_size_x, args.box_size_y, args.box_size_z)
