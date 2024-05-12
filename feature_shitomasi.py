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
import math 
from enum import Enum
import numpy as np 
import cv2


from parameters import Parameters  


class ShiTomasiDetector(object): 
    def __init__(self, num_features=Parameters.kNumFeatures, quality_level = 0.01, min_coner_distance = 3):
        self.num_features = num_features
        self.quality_level = quality_level
        self.min_coner_distance = min_coner_distance
        self.blockSize=5    # 3 is the default block size 
        print("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")

    def detect(self, frame, mask=None):                
        pts = cv2.goodFeaturesToTrack(frame, self.num_features, self.quality_level, self.min_coner_distance, blockSize=self.blockSize, mask=mask)
        # convert matrix of pts into list of keypoints 
        if pts is not None: 
            kps = [ cv2.KeyPoint(p[0][0], p[0][1], self.blockSize) for p in pts ]
        else:
            kps = []
        #if kVerbose:
        #    print('detector: Shi-Tomasi, #features: ', len(kps), ', #ref: ', self.num_features, ', frame res: ', frame.shape[0:2])      
        return kps

