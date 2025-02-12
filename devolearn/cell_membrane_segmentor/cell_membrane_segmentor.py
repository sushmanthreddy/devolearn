import torch
import torch.nn as nn
from torch.nn import functional as F
import torchvision
import torchvision.transforms as transforms
from torchvision.transforms import ToTensor
from torchvision.transforms import ToPILImage

import os
import cv2
import wget
import imutils
from tqdm import tqdm, tqdm_notebook
from PIL import Image
import numpy as np
from collections import deque
import pandas as pd
import matplotlib.pyplot as plt
import segmentation_models_pytorch as smp
import warnings
warnings.filterwarnings("ignore") 

from ..base_inference_engine import InferenceEngine

"""
3d segmentation model for C elegans embryo
"""

def generate_centroid_image(thresh, color_mode=False):
    """Used when centroid_mode is set to True
 
    Args:
        thresh (np.array): 2d numpy array that is returned from the segmentation model
        color_mode (bool, optional): If True, returns a 3 channel colored image. Dafaults to False.

    Returns:
        np.array : image containing the contours and their respective centroids 
        list : list of all centroids for the given image as [(x1,y1), (x2,y2)...]
    """

    thresh = cv2.blur(thresh, (5,5))
    thresh = thresh.astype(np.uint8)
    if color_mode == False:
        centroid_image = np.zeros(thresh.shape)
    else:
        centroid_image = np.zeros((thresh.shape[0], thresh.shape[1], 3))
        
    cnts = cv2.findContours(thresh, cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    cnts = imutils.grab_contours(cnts)
    centroids = []
    for c in cnts:
        try:
            # compute the center of the contour
            M = cv2.moments(c)
            cX = int(M["m10"] / M["m00"])
            cY = int(M["m01"] / M["m00"])
            # draw the contour and center of the shape on the image
            if color_mode == False:
                cv2.drawContours(centroid_image, [c], -1, (255, 255, 255), 2)
                cv2.circle(centroid_image, (cX, cY), 2, (255, 255, 255), -1)
            else:
                cv2.drawContours(centroid_image, [c], -1, (0, 0, 255), 2) #blue
                cv2.circle(centroid_image, (cX, cY), 2, (0, 255, 0), -1) #green
            centroids.append((cX, cY))
        except:
            pass

    return centroid_image, centroids

class cell_membrane_segmentor(InferenceEngine):
    def __init__(self, device = "cpu"):
        """Segments the c. elegans embryo from images/videos, 
        depends on segmentation-models-pytorch for the model backbone

        Args:
            device (str, optional): set to "cuda", runs operations on gpu and set to "cpu", runs operations on cpu. Defaults to "cpu".
        """
        
        self.device = device
        self.ENCODER = 'resnet18'
        self.ENCODER_WEIGHTS = 'imagenet'
        self.CLASSES = ["nucleus"]
        self.ACTIVATION = 'sigmoid'
        self.in_channels = 1
        self.model_url = "https://github.com/DevoLearn/devolearn/raw/master/devolearn/cell_membrane_segmentor/cell_membrane_segmentation_model.pth"
        self.model_name = "cell_membrane_segmentation_model.pth"
        self.model_dir = os.path.dirname(__file__)
        # print("at : ", os.path.dirname(__file__))

        self.model = smp.FPN(
                encoder_name= self.ENCODER, 
                encoder_weights= self.ENCODER_WEIGHTS, 
                classes=len(self.CLASSES), 
                activation= self.ACTIVATION,
                in_channels = self.in_channels 
            )


        self.download_checkpoint()
        self.model.to(self.device)
        self.model.eval()

        self.mini_transform = transforms.Compose([
                                     transforms.ToPILImage(),
                                     transforms.Resize((256,256), interpolation = Image.NEAREST),
                                     transforms.ToTensor(),
                                    ])


    def download_checkpoint(self):
        try:
            # print("model already downloaded, loading model...")
            self.model = torch.load(self.model_dir + "/" + self.model_name, map_location= self.device) 
        except:
            print("model not found, downloading from:", self.model_url)
            if os.path.isdir(self.model_dir) == False:
                os.mkdir(self.model_dir)
            filename = wget.download(self.model_url, out= self.model_dir)
            # print(filename)
            self.model = torch.load(self.model_dir + "/" + self.model_name, map_location= self.device) 

    def preprocess(self, image_grayscale_numpy):

        tensor = self.mini_transform(image_grayscale_numpy).unsqueeze(0).to(self.device)
        return tensor

    def predict(self, image_path, pred_size = (350,250), centroid_mode = False, color_mode = False):
        """Loads an image from image_path and converts it to grayscale, 
        then passes it through the model and returns centroids of the segmented features.
        reference{
            https://github.com/DevoLearn/devolearn#segmenting-the-c-elegans-embryo
        }

        Args:
            image_path (str): path to image
            pred_size (tuple, optional): size of output image,(width,height). Defaults to (350,250).
            centroid_mode (bool, optional): set to true to return both the segmented image and the list of centroids. Defaults to False.

        Returns:
            centroid_mode set to False:
                np.array : 1 channel image.
            centroid_mode set to True:
                color_mode set to False:
                    np.array : 1 channel image,
                    list : list of centroids.
                color_mode set to True:
                    np.array : 3 channel image,
                    list : list of centroids.
        """

        im = cv2.imread(image_path,0)

        tensor = self.preprocess(im)

        # The model has issues with the latest PyTorch versions, which causes
        # AttributeError: 'Upsample' object has no attribute 'recompute_scale_factor'
        # To avoid this, we manually set the recompute_scale_factor attribute to None
        self.model.segmentation_head[1].recompute_scale_factor = None
        res = self.model(tensor).detach().cpu().numpy()[0][0]
        
        res = cv2.resize(res,pred_size)
        if centroid_mode == False:
            return res
        else:
            centroid_image, centroids = generate_centroid_image(res, color_mode = color_mode)
            return centroid_image, centroids

    def predict_from_video(self, video_path, pred_size = (350,250), save_folder = "preds", centroid_mode = False, color_mode = False, notebook_mode = False):
        """Splits a video from video_path into frames and passes the 
        frames through the model for predictions. Saves predicted images in save_folder.
        And optionally saves all the centroid predictions into a pandas.DataFrame. 

        Args:
            video_path (str): path to the video file.
            pred_size (tuple, optional): size of output image,(width,height). Defaults to (350,250).
            save_folder (str, optional): path to folder to be saved in. Defaults to "preds".
            centroid_mode (bool, optional): set to true to return both the segmented image and the list of centroids. Defaults to False.
            color_mode (bool, optional): set to true to return a color image. Defaults to False.
            notebook_mode (bool, optional): toogle between script(False) and notebook(True), for better user interface. Defaults to False.

        Returns:
            centroid_mode set to True:
                pd.DataFrame : containing file name and their centriods
            centroid_mode set to False:
                list : list containing the names of the entries in the save_folder directory 
        """
        
        vidObj = cv2.VideoCapture(video_path)   
        success = 1
        images = deque()
        count = 0

        if centroid_mode == True:
            filenames_centroids = []

        while success: 
            success, image = vidObj.read() 
            
            try:
                image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
                images.append(image)
                 
            except:
                # print("skipped possible corrupt frame number : ", count)
                pass
            count += 1 
        
        if os.path.isdir(save_folder) == False:
            os.mkdir(save_folder)

        if notebook_mode == True:
            for i in tqdm_notebook(range(len(images)), desc = "saving predictions: "):    
                save_name = save_folder + "/" + str(i) + ".jpg"
                tensor = self.mini_transform(images[i]).unsqueeze(0).to(self.device)
                res = self.model(tensor).detach().cpu().numpy()[0][0]

                if centroid_mode == True:
                    res, centroids = generate_centroid_image(res, color_mode = color_mode)
                    filenames_centroids.append([save_name, centroids])

                res = cv2.resize(res,pred_size)
                cv2.imwrite(save_name, res*255)
        else :
            for i in tqdm(range(len(images)), desc = "saving predictions: "):
                save_name = save_folder + "/" + str(i) + ".jpg"
                tensor = self.mini_transform(images[i]).unsqueeze(0).to(self.device)
                res = self.model(tensor).detach().cpu().numpy()[0][0]

                if centroid_mode == True:
                    res, centroids = generate_centroid_image(res, color_mode = color_mode)
                    filenames_centroids.append([save_name, centroids])

                res = cv2.resize(res,pred_size)
                cv2.imwrite(save_name, res*255)

        if centroid_mode == True:
            df = pd.DataFrame(filenames_centroids, columns = ["filenames", "centroids"])
            return df
        else:
            return os.listdir(save_folder)
