#!/usr/bin/env python  
# -*- coding:utf-8 _*-
""" 
@author: Liu Shien
@file: ligprep.py
@time: 2021/4/1/16:28
"""
import argparse

from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem.EnumerateStereoisomers import EnumerateStereoisomers, StereoEnumerationOptions
from rdkit.Chem.MolStandardize import rdMolStandardize
import os


class LigPrep:
    def __init__(self, infile, workdir):
        self.infile = infile
        self.workdir = workdir
        self.mol_dict = {}

    def parse_infile(self):
        with open(self.infile, "r") as inf:
            for line in inf:
                tmp = line.strip().split("\t")
                if len(tmp) < 2:
                    continue
                smi = tmp[0]
                id1 = tmp[1]

                mol = Chem.MolFromSmiles(smi)
                if mol is None:
                    continue
                mol.SetProp("_Name", id1)
                self.mol_dict[id1] = mol

    def setero(self, mol, onlyUnassigned=True):
        if onlyUnassigned:
            opts = StereoEnumerationOptions(tryEmbedding=True)
        else:
            opts = StereoEnumerationOptions(tryEmbedding=True, onlyUnassigned=False)
        isomers = tuple(EnumerateStereoisomers(mol, options=opts))
        res = []
        if len(isomers) > 1:
            for idx, tmp in enumerate(isomers):
                name = tmp.GetProp("_Name") + "-CC" + str(idx)
                tmp.SetProp("_Name", name)
                res.append(tmp)
            return res
        else:
            return list(isomers)

    def tau(self, mol, can=True):
        params = rdMolStandardize.CleanupParameters()
        params.maxTautomers = 1000
        params.maxTransforms = 10000
        enumerator = rdMolStandardize.TautomerEnumerator(params)
        try:
            canon = enumerator.Canonicalize(mol)
        except Exception as e:
            print(e)
            return [mol]

        if can:
            return [canon]
        csmi = Chem.MolToSmiles(canon)
        res = [canon]
        tauts = enumerator.Enumerate(mol)
        smis = [Chem.MolToSmiles(x) for x in tauts]
        stpl = sorted((x, y) for x, y in zip(smis, tauts) if x != csmi)
        res += [y for x, y in stpl]

        new = []
        for idx, tmp in enumerate(res):
            name = tmp.GetProp("_Name") + "-CT" + str(idx)
            tmp.SetProp("_Name", name)
            new.append(tmp)

        return new

    def to_3D(self, mol):
        mol = Chem.AddHs(mol)
        AllChem.EmbedMolecule(mol, useExpTorsionAnglePrefs=True, useBasicKnowledge=True, maxAttempts=10000,
                              useRandomCoords=True)
        if mol.GetNumConformers() > 0:
            AllChem.UFFOptimizeMolecule(mol, 200, 10.0, -1)
            return mol
        else:
            return None

    def gen_minimized_3D(self, path, rdmol, addH=True):
        """
        generate 3d structure with lower energy
        :param rdmol: rdkit molecule object
        :param addH: flag to add hydrogen atoms
        :return: flag to generate successfully
        :rtype: bool
        """
        from rdkit.Chem import AllChem
        from rdkit.Chem import rdDistGeom
        from rdkit.Chem import rdMolAlign
        from openbabel import pybel
        name = rdmol.GetProp("_Name")
        sdf_path = os.path.join(path, name + ".sdf")
        writer = Chem.SDWriter(sdf_path)
        if addH:
            rdmol = Chem.AddHs(rdmol, addCoords=True)

        param = rdDistGeom.ETKDGv2()
        param.pruneRmsThresh = 0.3
        cids = rdDistGeom.EmbedMultipleConfs(rdmol, 50, param)
        mp = AllChem.MMFFGetMoleculeProperties(rdmol, mmffVariant='MMFF94s')
        AllChem.MMFFOptimizeMoleculeConfs(rdmol, numThreads=0, mmffVariant='MMFF94s')
        res = []
        for cid in cids:
            ff = AllChem.MMFFGetMoleculeForceField(rdmol, mp, confId=cid)
            # ff.Initialize()
            ff.Minimize()
            e = ff.CalcEnergy()
            res.append((cid, e))
        sorted_res = sorted(res, key=lambda x: x[1])
        rdMolAlign.AlignMolConformers(rdmol)
        new = Chem.Mol(rdmol)
        new.RemoveAllConformers()
        min_conf = rdmol.GetConformer(sorted_res[0][0])
        new.AddConformer(min_conf)

        writer.write(new)
        writer.close()
        num = 0
        for mol in pybel.readfile("sdf", sdf_path):
            mol.removeh()
            # mol.OBMol.CorrectForPH(7.4)
            mol.OBMol.AddHydrogens(False, True, 7.4)
            mol.localopt(forcefield='mmff94', steps=500)
            mol.write("pdbqt", "{}.pdbqt".format(os.path.join(path, name)))
            num += 1

        return num == 1

    def process(self):
        # create a dir
        path = os.path.join(self.workdir, "ligands_for_vina")

        self.parse_infile()
        for gid in self.mol_dict:
            mol = self.mol_dict[gid]
            mystereo = self.setero(mol)

            mytau = []
            for stereo in mystereo:
                tmp = self.tau(stereo)
                mytau += tmp

            for newmol in mytau:
                if newmol is not None:
                    try:
                        self.gen_minimized_3D(path, newmol)
                    except Exception as e:
                        print(e)
                        continue


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="LigPrep @dalong")
    parser.add_argument("workdir", help="Workdir")
    parser.add_argument("mols_smi", help="Seed fragments")

    args = parser.parse_args()
    lig = LigPrep(args.mols_smi, args.workdir)
    lig.parse_infile()
    lig.process()
