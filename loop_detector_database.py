"""
* This file is part of PYSLAM 
*
* Copyright (C) 2016-present Luigi Freda <luigi dot freda at gmail dot com> 
*
* PYSLAM is free software: you can redistribute it and/or modify
* it under the terms of the GNU General Public License as published by
* the Free Software Foundation, either version 3 of the License, or
* (at your option) any later version.
*
* PYSLAM is distributed in the hope that it will be useful,
* but WITHOUT ANY WARRANTY; without even the implied warranty of
* MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
* GNU General Public License for more details.
*
* You should have received a copy of the GNU General Public License
* along with PYSLAM. If not, see <http://www.gnu.org/licenses/>.
"""


import os
import time
import math 
import numpy as np
import cv2
import sys
from enum import Enum

from utils_sys import getchar, Printer 

from typing import List

from parameters import Parameters
import torch
from pyflann import FLANN
import faiss

import traceback


kVerbose = True

kMinDeltaFrameForMeaningfulLoopClosure = Parameters.kMinDeltaFrameForMeaningfulLoopClosure
kMaxResultsForLoopClosure = Parameters.kMaxResultsForLoopClosure

kTimerVerbose = False

kScriptPath = os.path.realpath(__file__)
kScriptFolder = os.path.dirname(kScriptPath)
kRootFolder = kScriptFolder
kDataFolder = kRootFolder + '/data'


if Parameters.kLoopClosingDebugAndPrintToFile:
    from loop_detector_base import print


class SCoreType(Enum):
    COSINE = 0
    SAD = 1


class ScoreBase:
    def __init__(self, type, worst_score, best_score):
        self.type = type
        self.worst_score = worst_score
        self.best_score = best_score
    
    # g_des1 is [1, D], g_des2 is [M, D]
    def __call__(self, g_des1, g_des2):
        pass


class ScoreSad(ScoreBase):
    def __init__(self):
        super().__init__(SCoreType.SAD, worst_score=-sys.float_info.max, best_score=0.0)
        
    @staticmethod
    def score(g_des1, g_des2):
        diff = g_des1-g_des2
        is_nan_diff = np.isnan(diff)
        nan_count_per_row = np.count_nonzero(is_nan_diff, axis=1)
        dim = diff.shape[1] - nan_count_per_row
        #print(f'dim: {dim}, diff.shape: {diff.shape}')
        diff[is_nan_diff] = 0
        return -np.sum(np.abs(diff),axis=1) / dim   # invert the sign of the standard SAD score
        
    # g_des1 is [1, D], g_des2 is [M, D]
    def __call__(self, g_des1, g_des2):
        return self.score(g_des1, g_des2)


class ScoreCosine(ScoreBase):
    def __init__(self):
        super().__init__(SCoreType.COSINE, worst_score=-1.0, best_score=1.0)
  
    @staticmethod
    def score(g_des1, g_des2):
        norm_g_des1 = np.linalg.norm(g_des1, axis=1, keepdims=True)  # g_des1 is [1, D], so norm is scalar
        norm_g_des2 = np.linalg.norm(g_des2, axis=1, keepdims=True)  # g_des2 is [M, D]
        dot_product = np.dot(g_des2, g_des1.T).ravel()
        cosine_similarity = dot_product / (norm_g_des1 * norm_g_des2.ravel())
        return cosine_similarity.ravel()
      
    # g_des1 is [1, D], g_des2 is [M, D]
    def __call__(self, g_des1, g_des2):
        return self.score(g_des1, g_des2)
  

class ScoreTorchCosine(ScoreBase):
    def __init__(self):
        super().__init__(SCoreType.COSINE, worst_score=-1.0, best_score=1.0)
  
    @staticmethod
    def score(g_des1, g_des2):
        # Ensure g_des1 is a 2D tensor of shape [1, D]
        if g_des1.dim() == 1:
            g_des1 = g_des1.unsqueeze(0)

        # Compute the norms
        norm_g_des1 = g_des1.norm(dim=1, keepdim=True)  # Shape [1, 1]
        norm_g_des2 = g_des2.norm(dim=1, keepdim=True)  # Shape [M, 1]

        # Dot product between g_des1 and each row of g_des2
        dot_product = torch.mm(g_des2, g_des1.T).squeeze()  # Shape [M]

        # Compute cosine similarity
        cosine_similarity = (dot_product / (norm_g_des1 * norm_g_des2).squeeze()).ravel()
        return cosine_similarity

    # g_des1 is [1, D], g_des2 is [M, D]
    def __call__(self, g_des1, g_des2):
        return self.score(g_des1, g_des2)
    


# abstract class
class Database:
    def __init__(self, score=ScoreCosine()):
        self.global_des_database = None
        self.score = score
    
    def query(self, g_des, max_num_results=kMaxResultsForLoopClosure): 
        raise NotImplementedError
    
    # add image descriptors to global_des_database
    def add(self, g_des): 
        raise NotImplementedError
    
    def reset(self):
        pass
    

# Simple database implementation with numpy entries
class SimpleDatabase(Database): 
    def __init__(self, score=ScoreCosine()):
        self.global_des_database = []
        self.score = score

    def query(self, g_des, max_num_results=kMaxResultsForLoopClosure):
        if g_des.ndim == 1:
            g_des = g_des.reshape(1, -1)    
        descriptor_dim = g_des.shape[1]
        global_des_database = np.array(self.global_des_database).reshape(-1, descriptor_dim)
        score = self.score(g_des, global_des_database) 
        best_idxs = np.argsort(-score)[:max_num_results+1]
        best_scores = score[best_idxs[1:]]
        return np.array(best_idxs), np.array(best_scores)
    
    # add image descriptors to global_des_database
    def add(self, g_des): 
        self.global_des_database.append(g_des)
        
    def reset(self):
        self.global_des_database.clear()


# Similar to SimpleDatabase but with torch entries
class SimpleTorchDatabase(Database): 
    def __init__(self, score=ScoreTorchCosine()):
        self.global_des_database = []
        self.score = score

    def query(self, g_des, max_num_results=kMaxResultsForLoopClosure):
        # Ensure g_des is 2D for scoring
        if g_des.dim() == 1:
            g_des = g_des.unsqueeze(0)  # Convert to shape (1, descriptor_dim)
        descriptor_dim = g_des.shape[-1]  # Get the last dimension size
        
        # Stack the database descriptors into a single tensor for comparison
        if self.global_des_database:
            global_des_database = torch.stack(self.global_des_database).reshape(-1, descriptor_dim)
            score = self.score(g_des, global_des_database)  # Assuming score can handle PyTorch tensors
            
            max_num_results = min(max_num_results+1, global_des_database.shape[0])
            # Get the indices of the top matches and their corresponding scores
            _, best_idxs = torch.topk(score, max_num_results, largest=True)
            best_scores = score[best_idxs[1:]]  # Skip the top result if it's the query itself
        else:
            best_idxs = torch.tensor([], dtype=torch.long)
            best_scores = torch.tensor([])

        return best_idxs.cpu().numpy(), best_scores.cpu().numpy()
    
    # Add image descriptors to global_des_database
    def add(self, g_des): 
        if isinstance(g_des, torch.Tensor):
            self.global_des_database.append(g_des)
        else:
            raise TypeError("Descriptor must be a PyTorch tensor")
        
    def reset(self):
        self.global_des_database.clear()
        

class FlannDatabase(Database):
    def __init__(self, score=ScoreCosine(), rebuild_threshold=100): 
        self.global_des_database = []
        self.recent_descriptors = []
        self.score = score
        self.flann = FLANN()
        self.flann_index = None
        self.index_built = False
        self.rebuild_threshold = rebuild_threshold
        self.des_dim = None
        self.num_trees = None

    def reset(self):
        self.global_des_database.clear()
        self.recent_descriptors.clear()
        self.flann = FLANN()
        self.flann_index = None
        self.index_built = False
    
    def build_index(self):
        assert(self.num_trees is not None)        
        assert(self.des_dim is not None)
        if len(self.global_des_database)>0:
            print('FlannFastDatabase: building index...')
            if not isinstance(self.global_des_database, np.ndarray):
                self.global_des_database = np.array(self.global_des_database).reshape(-1, self.des_dim) 
            self.flann_index = self.flann.build_index(self.global_des_database, algorithm="kdtree", trees=self.num_trees)
            self.index_built = True

    def select_num_trees(self, des_dim):
        if des_dim <= 10:
            return 8
        elif des_dim <= 50:
            return 16
        elif des_dim <= 100:
            return 32
        else:
            return 64

    def add(self, g_des):
        if self.des_dim is None:
            self.des_dim = len(g_des.ravel())
            self.num_trees = self.select_num_trees(self.des_dim)
        self.recent_descriptors.append(g_des)
        if len(self.recent_descriptors) >= self.rebuild_threshold:
            # Consolidate recent descriptors into the main database and rebuild index
            if len(self.global_des_database) > 0:
                self.global_des_database = np.array(self.global_des_database).reshape(-1, self.des_dim) 
                num_global_descriptors = self.global_des_database.shape[0]                      
            else: 
                num_global_descriptors = 0
            self.recent_descriptors = np.array(self.recent_descriptors).reshape(-1, self.des_dim)
            #print(f'global_des_database.shape: {self.global_des_database.shape}, recent_descriptors.shape: {self.recent_descriptors.shape}')
            self.global_des_database = np.vstack((self.global_des_database, self.recent_descriptors)) if num_global_descriptors>0 else self.recent_descriptors
            self.recent_descriptors = []
            self.build_index()

    def query(self, g_des, max_num_results=kMaxResultsForLoopClosure):
        all_descriptors = []
        all_idxs = []
        main_idxs = []
        
        recent_descriptors = np.array(self.recent_descriptors).reshape(-1, self.des_dim)
        num_recent_descriptors = recent_descriptors.shape[0] 
                    
        # Use FLANN for main database if it exists
        if self.index_built:
            flann_idxs, flann_dists = self.flann.nn_index(g_des, num_neighbors=2*max_num_results) # more than needed for the limited flann accuracy
            main_idxs = flann_idxs.ravel()
            flann_dists = flann_dists.ravel()
            #print(f'flann_idxs: {flann_idxs}, flann_dists: {flann_dists}')
            
            # Remove the trivial self-match            
            if flann_dists[0] == 0:
                main_idxs = main_idxs[1:]
                
            main_descriptors = self.global_des_database[main_idxs,:]
            num_global_descriptors = self.global_des_database.shape[0]            
            #print(f'main_descriptors.shape: {main_descriptors.shape}, recent_descriptors.shape: {recent_descriptors.shape}')            
            all_descriptors = np.vstack((main_descriptors, recent_descriptors)) if num_recent_descriptors > 0 else main_descriptors
            #print(f'main_idxs.shape: {main_idxs.shape}, np.arange(num_recent_descriptors).shape: {np.arange(num_recent_descriptors).shape}')
            idxs_recent_descriptors = np.arange(num_global_descriptors, num_global_descriptors + num_recent_descriptors)                
            all_idxs = np.concatenate((main_idxs, idxs_recent_descriptors)) if num_recent_descriptors > 0 else main_idxs
        else:
            if len(self.recent_descriptors) == 0:
                return np.array([]), np.array([])
            # Only recent descriptors are present         
            all_descriptors = np.vstack(recent_descriptors)
            all_idxs = np.arange(num_recent_descriptors)
        
        # Compute scores for all descriptors
        all_scores = self.score(g_des, all_descriptors)
        
        # Sort by score
        sorted_idxs = np.argsort(-all_scores)[:max_num_results + 1]
        best_idxs = all_idxs[sorted_idxs]
        best_scores = all_scores[sorted_idxs]
        
        # Check for and remove self-match if present
        if len(best_idxs) > 0 and abs(best_scores[0] - self.score.best_score) < 1e-6:  # Assumes self-match is the highest score
            best_idxs = best_idxs[1:max_num_results + 1]
        else:
            best_idxs = best_idxs[:max_num_results]
        best_scores = all_scores[sorted_idxs]   
             
        return np.array(best_idxs), np.array(best_scores)


# See https://github.com/facebookresearch/faiss
class FaissDatabase(Database):
    def __init__(self, score=ScoreCosine(), rebuild_threshold=50):
        self.global_des_database = []
        self.recent_descriptors = []
        self.score = score
        self.index = None
        self.index_built = False
        self.rebuild_threshold = rebuild_threshold
        self.des_dim = None

    def reset(self):
        self.global_des_database.clear()
        self.recent_descriptors.clear()
        self.index = None
        self.index_built = False

    def build_index(self):
        assert self.des_dim is not None
        if len(self.global_des_database) > 0:
            print('FaissDatabase: building index...')
            # Convert to numpy array
            self.global_des_database = np.array(self.global_des_database).astype(np.float32).reshape(-1, self.des_dim)
            # Build the FAISS index
            self.index = faiss.IndexFlatL2(self.des_dim)  # Using L2 distance for the index
            self.index.add(self.global_des_database)  # Add the global descriptors to the index
            self.index_built = True

    def add(self, g_des):
        if self.des_dim is None:
            self.des_dim = len(g_des.ravel())
        self.recent_descriptors.append(g_des)
        if len(self.recent_descriptors) >= self.rebuild_threshold:
            # Consolidate recent descriptors into the main database and rebuild index
            if len(self.global_des_database) > 0:
                self.global_des_database = np.array(self.global_des_database).reshape(-1, self.des_dim) 
                num_global_descriptors = self.global_des_database.shape[0]                      
            else: 
                num_global_descriptors = 0
            self.recent_descriptors = np.array(self.recent_descriptors).reshape(-1, self.des_dim)
            #print(f'global_des_database.shape: {self.global_des_database.shape}, recent_descriptors.shape: {self.recent_descriptors.shape}')
            self.global_des_database = np.vstack((self.global_des_database, self.recent_descriptors)) if num_global_descriptors>0 else self.recent_descriptors
            self.recent_descriptors = []
            self.build_index()

    def query(self, g_des, max_num_results=kMaxResultsForLoopClosure):
        all_descriptors = []
        all_idxs = []

        recent_descriptors = np.array(self.recent_descriptors).reshape(-1, self.des_dim)
        num_recent_descriptors = recent_descriptors.shape[0]
        
        # Use FAISS for main database if it exists
        if self.index_built:
            g_des = np.array(g_des).astype(np.float32).reshape(1, -1)  # Ensure correct shape for query
            D, I = self.index.search(g_des, 2 * max_num_results)  # Searching for more than needed for robustness
            main_idxs = I.ravel()
            main_dists = D.ravel()
            #print(f'main_idxs: {main_idxs}, main_dists: {main_dists}')

            # Remove the trivial self-match
            if main_dists[0] == 0:
                main_idxs = main_idxs[1:]

            main_descriptors = self.global_des_database[main_idxs, :]
            num_global_descriptors = self.global_des_database.shape[0]
            
            all_descriptors = np.vstack((main_descriptors, recent_descriptors)) if num_recent_descriptors > 0 else main_descriptors
            
            idxs_recent_descriptors = np.arange(num_global_descriptors, num_global_descriptors + num_recent_descriptors)
            all_idxs = np.concatenate((main_idxs, idxs_recent_descriptors)) if num_recent_descriptors > 0 else main_idxs
        else:
            if len(self.recent_descriptors) == 0:
                return np.array([]), np.array([])            
            # Only recent descriptors are present
            all_descriptors = np.vstack(recent_descriptors)
            all_idxs = np.arange(len(recent_descriptors))

        # Compute scores for all descriptors
        all_scores = self.score(g_des, all_descriptors)

        # Sort by score
        sorted_idxs = np.argsort(-all_scores)[:max_num_results + 1]
        best_idxs = all_idxs[sorted_idxs]
        best_scores = all_scores[sorted_idxs]

        # Check for and remove self-match if present
        if len(best_idxs) > 0 and abs(best_scores[0] - self.score.best_score) < 1e-6:  # Assumes self-match is the highest score
            best_idxs = best_idxs[1:max_num_results + 1]
        else:
            best_idxs = best_idxs[:max_num_results]
        best_scores = best_scores[:max_num_results]

        return np.array(best_idxs), np.array(best_scores)
