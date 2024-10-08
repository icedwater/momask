import visualization.Animation as Animation

from visualization.InverseKinematics import BasicInverseKinematics, BasicJacobianIK, InverseKinematics
from visualization.Quaternions import Quaternions
import visualization.BVH_mod as BVH
from visualization.remove_fs import *

from utils.plot_script import plot_3d_motion
from utils import paramUtil
from common.skeleton import Skeleton
import torch

from torch import nn
from visualization.utils.quat import ik_rot, between, fk, ik
from tqdm import tqdm

from pathlib import Path


def get_grot(glb, parent, offset):
    root_quat = np.array([[1.0, 0.0, 0.0, 0.0]]).repeat(glb.shape[0], axis=0)[:, None]
    local_pos = glb[:, 1:] - glb[:, parent[1:]]
    norm_offset = offset[1:] / np.linalg.norm(offset[1:], axis=-1, keepdims=True)
    norm_lpos = local_pos / np.linalg.norm(local_pos, axis=-1, keepdims=True)
    grot = between(norm_offset, norm_lpos)
    grot = np.concatenate((root_quat, grot), axis=1)
    grot /= np.linalg.norm(grot, axis=-1, keepdims=True)
    return grot


class Joint2BVHConvertor:
    def __init__(self):
        self.template = BVH.load('./visualization/data/template.bvh', need_quater=True)
        self.re_order = [0, 1, 4, 7, 10, 2, 5, 8, 11, 3, 6, 9, 12, 15, 13, 16, 18, 20, 14, 17, 19, 21]

        self.re_order_inv = [0, 1, 5, 9, 2, 6, 10, 3, 7, 11, 4, 8, 12, 14, 18, 13, 15, 19, 16, 20, 17, 21]
        self.end_points = [4, 8, 13, 17, 21]

        self.template_offset = self.template.offsets.copy()
        self.parents = [-1, 0, 1, 2, 3, 0, 5, 6, 7, 0, 9, 10, 11, 12, 11, 14, 15, 16, 11, 18, 19, 20]

    def convert(self, positions, filename, iterations=10, foot_ik=True):
        '''
        Convert the SMPL joint positions to Mocap BVH
        :param positions: (N, 22, 3)
        :param filename: Save path for resulting BVH
        :param iterations: iterations for optimizing rotations, 10 is usually enough
        :param foot_ik: whether to enfore foot inverse kinematics, removing foot slide issue.
        :return:
        '''
        positions = positions[:, self.re_order]
        new_anim = self.template.copy()
        new_anim.rotations = Quaternions.id(positions.shape[:-1])
        new_anim.positions = new_anim.positions[0:1].repeat(positions.shape[0], axis=-0)
        new_anim.positions[:, 0] = positions[:, 0]

        if foot_ik:
            positions = remove_fs(positions, None, fid_l=(3, 4), fid_r=(7, 8), interp_length=5,
                                  force_on_floor=True)
        ik_solver = BasicInverseKinematics(new_anim, positions, iterations=iterations, silent=True)
        new_anim = ik_solver()

        # BVH.save(filename, new_anim, names=new_anim.names, frametime=1 / 20, order='zyx', quater=True)
        glb = Animation.positions_global(new_anim)[:, self.re_order_inv]
        if filename is not None:
            BVH.save(filename, new_anim, names=new_anim.names, frametime=1 / 20, order='zyx', quater=True)
        return new_anim, glb

    def convert_sgd(self, positions, filename, iterations=100, foot_ik=True):
        '''
        Convert the SMPL joint positions to Mocap BVH

        :param positions: (N, 22, 3)
        :param filename: Save path for resulting BVH
        :param iterations: iterations for optimizing rotations, 10 is usually enough
        :param foot_ik: whether to enfore foot inverse kinematics, removing foot slide issue.
        :return:
        '''

        ## Positional Foot locking ##
        glb = positions[:, self.re_order]

        if foot_ik:
             glb = remove_fs(glb, None, fid_l=(3, 4), fid_r=(7, 8), interp_length=2,
                                 force_on_floor=True)

        ## Fit BVH ##
        new_anim = self.template.copy()
        new_anim.rotations = Quaternions.id(glb.shape[:-1])
        new_anim.positions = new_anim.positions[0:1].repeat(glb.shape[0], axis=-0)
        new_anim.positions[:, 0] = glb[:, 0]
        anim = new_anim.copy()

        rot = torch.tensor(anim.rotations.qs, dtype=torch.float)
        pos = torch.tensor(anim.positions[:, 0, :], dtype=torch.float)
        offset = torch.tensor(anim.offsets, dtype=torch.float)

        glb = torch.tensor(glb, dtype=torch.float)
        ik_solver = InverseKinematics(rot, pos, offset, anim.parents, glb)
        print('Fixing foot contact using IK...')
        for i in tqdm(range(iterations)):
            mse = ik_solver.step()
            # print(i, mse)

        rotations = ik_solver.rotations.detach().cpu()
        norm = torch.norm(rotations, dim=-1, keepdim=True)
        rotations /= norm

        anim.rotations = Quaternions(rotations.numpy())
        anim.rotations[:, self.end_points] = Quaternions.id((anim.rotations.shape[0], len(self.end_points)))
        anim.positions[:, 0, :] = ik_solver.position.detach().cpu().numpy()
        if filename is not None:
            BVH.save(filename, anim, names=new_anim.names, frametime=1 / 20, order='zyx', quater=True)
        # BVH.save(filename[:-3] + 'bvh', anim, names=new_anim.names, frametime=1 / 20, order='zyx', quater=True)
        glb = Animation.positions_global(anim)[:, self.re_order_inv]
        return anim, glb

def example():
    """
    Obviously will not work until the file exists, but this just shows syntax.
    Here we assume the Numpy array to be just data, not an object.
    """
    # file = 'batch2_sample13_repeat0_len196.npy'
    # TODO: figure out why these lines in convert() are important:
    ### (51?) new_anim.positions = lpos
    ### (52?) new_anim.positions[0:1].repeat(positions.shape[0], axis=-0)
    converter = Joint2BVHConvertor()
    example_npy = "batch1_sample12_repeat0_len196.npy"      # just using the karate example
    path = f"/path/to/joints/{example_npy}"
    joints = np.load(path)
    new_anim = converter.convert(joints, "./gen_L196.mp4", foot_ik=True)


def batch_example():
    """
    Again another non-working syntax example, this time for a full folder.
    """
    converter = Joint2BVHConvertor()
    folder = "/path/to/folder_of_npys"
    files = os.listdir(os.path.join(folder, 'joints'))
    files = [f for f in files if 'repeat' in f]
    converter = Joint2BVHConvertor()
    for f in tqdm(files):
        joints = np.load(os.path.join(folder, 'joints', f))
        converter.convert(joints, os.path.join(folder, 'ik_animations', f'ik_{f}'.replace('npy', 'mp4')), foot_ik=True)

def convert_one_result(npy_dir: str, sample :int=0, rep :int=0, converter :Joint2BVHConvertor=None, foot_ik :bool=True):
    """
    Given a directory, a sample, and a rep, convert its result to a BVH.
    BVH can be previewed in https://vrm-c.github.io/bvh2vrma or Blender.

    :param npy_dir:     path to the directory containing the relevant results.npy
    :param sample:      index number of the sample to be considered, default 0
    :param rep:         index number of the rep to be considered, default 0
    :param converter:   Joint2BVHConvertor that does the actual conversion
    :param foot_ik:     whether or not to use foot_ik
    """
    path = Path(f"{npy_dir}/results.npy")
    results = np.load(path, allow_pickle=True).item()
    motion = results.get("motion")

    joints = motion[rep, sample].transpose(2, 0, 1)
    output_path = f"{npy_dir}/sample{sample:02d}_rep{rep:02d}.bvh"
    converter.convert(joints, output_path, foot_ik=foot_ik)


def main():
    converter = Joint2BVHConvertor()
    folder = ""
    convert_one_result(npy_dir=folder, converter=converter, foot_ik=True)

if __name__ == "__main__":
    main()
