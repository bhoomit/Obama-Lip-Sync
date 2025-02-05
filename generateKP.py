# # Get keypoint data from the images and prepare for pix2pix
# from utils import *
''''''
from __future__ import division, print_function

import os
import pickle as pkl
import subprocess
from glob import glob

import matplotlib.pyplot as plt
import numpy as np
import scipy.io.wavfile as wav
from skimage import io
from sklearn.decomposition import PCA
from sklearn.preprocessing import MinMaxScaler
from tqdm import tqdm

import cv2
import dlib
import imutils
import keras
import keras.backend as K
import pyworld as pw
import soundfile as sf
from imutils import face_utils
from keras.layers import (LSTM, Dense, Dropout, Embedding, Lambda,
                          TimeDistributed)
from keras.models import Sequential, load_model
from keras.preprocessing.sequence import pad_sequences
from python_speech_features import logfbank

predictor_path = 'data/shape_predictor_68_face_landmarks.dat'

detector = dlib.get_frontal_face_detector()
predictor = dlib.shape_predictor(predictor_path)

#####################################################################

n_batch = 1  # Conservative guess on the batchsize
time_delay = 10
length = 60  # More than this and the LSTM becomes retard

#####################################################################

# trim_time = 3.0
# delay_in_seconds = 0 # 0.200
# time_delay = int(np.ceil(delay_in_seconds * 100)) # 150 ms and 200 fps
# limit_up = int(trim_time*100) # 500
# train_val_ratio = 0.8
# batchSize = 100


def get_facial_landmarks(filename):
    image = io.imread(filename)
    # detect face(s)
    dets = detector(image, 1)
    shape = np.empty([1, 1])
    for k, d in enumerate(dets):
        # Get the landmarks/parts for the face in box d.
        shape = predictor(image, d)
        shape = face_utils.shape_to_np(shape)

    return shape


def get_facial_landmarks_img(img):
    # detect face(s)
    dets = detector(img, 1)
    shape = np.empty([1, 1])
    for k, d in enumerate(dets):
        # Get the landmarks/parts for the face in box d.
        shape = predictor(img, d)
        shape = face_utils.shape_to_np(shape)

    return shape


def getTilt(keypoints_mn):
    # Remove in plane rotation using the eyes
    eyes_kp = np.array(keypoints_mn[36:47])
    x = eyes_kp[:, 0]
    y = -1 * eyes_kp[:, 1]
    # print('X:', x)
    # print('Y:', y)
    m = np.polyfit(x, y, 1)
    tilt = np.degrees(np.arctan(m[0]))
    return tilt


def drawLips(keypoints, new_img, c=(255, 255, 255), th=1, show=False):
    keypoints = np.float32(keypoints)

    for i in range(48, 59):
        cv2.line(new_img,
                 tuple(keypoints[i]),
                 tuple(keypoints[i + 1]),
                 color=c,
                 thickness=th)
    cv2.line(new_img,
             tuple(keypoints[48]),
             tuple(keypoints[59]),
             color=c,
             thickness=th)
    cv2.line(new_img,
             tuple(keypoints[48]),
             tuple(keypoints[60]),
             color=c,
             thickness=th)
    cv2.line(new_img,
             tuple(keypoints[54]),
             tuple(keypoints[64]),
             color=c,
             thickness=th)
    cv2.line(new_img,
             tuple(keypoints[67]),
             tuple(keypoints[60]),
             color=c,
             thickness=th)
    for i in range(60, 67):
        cv2.line(new_img,
                 tuple(keypoints[i]),
                 tuple(keypoints[i + 1]),
                 color=c,
                 thickness=th)

    if (show == True):
        cv2.imshow('lol', new_img)
        cv2.waitKey(10000)


def getKeypointFeatures(keypoints):
    # Mean Normalize the keypoints wrt the center of the mouth
    # Leads to face position invariancy
    mouth_kp_mean = np.average(keypoints[48:67], 0)
    keypoints_mn = keypoints - mouth_kp_mean
    if (len(keypoints_mn) == 1):
        return None
    # Remove tilt
    x_dash = keypoints_mn[:, 0]
    y_dash = keypoints_mn[:, 1]
    theta = np.deg2rad(getTilt(keypoints_mn))
    c = np.cos(theta)
    s = np.sin(theta)
    x = x_dash * c - y_dash * s  # x = x'cos(theta)-y'sin(theta)
    y = x_dash * s + y_dash * c  # y = x'sin(theta)+y'cos(theta)
    keypoints_tilt = np.hstack((x.reshape((-1, 1)), y.reshape((-1, 1))))

    # Normalize
    N = np.linalg.norm(keypoints_tilt, 2)
    return [keypoints_tilt / N, N, theta, mouth_kp_mean]


def getOriginalKeypoints(kp_features_mouth, N, tilt, mean):
    # Denormalize the points
    kp_dn = N * kp_features_mouth
    # Add the tilt
    x, y = kp_dn[:, 0], kp_dn[:, 1]
    c, s = np.cos(tilt), np.sin(tilt)
    x_dash, y_dash = x * c + y * s, -x * s + y * c
    kp_tilt = np.hstack((x_dash.reshape((-1, 1)), y_dash.reshape((-1, 1))))
    # Shift to the mean
    kp = kp_tilt + mean
    return kp


def get_sec(time_str):
    h, m, s = time_str.split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


####################################################################################


# The model
def LSTM_lipsync(in_shape=(n_batch, length, 26), out_shape=(length, 8)):
    # model = Sequential()
    # model.add(LSTM(256, batch_input_shape=in_shape, return_sequences=True)) #, stateful=True))
    # model.add(TimeDistributed(Dense(out_shape[1])))
    # model.compile(loss='mean_squared_error', optimizer='adam')
    # print(model.summary())
    # return model

    model = Sequential()
    model.add(LSTM(8, input_shape=(length, 26)))
    model.compile(loss='mean_squared_error', optimizer='adam')
    print(model.summary())
    return model


def batchify(X, n_batch):  # X is a 3D array
    X = np.array(X)
    n = X.shape[0] % n_batch
    # print('n:', n, 'sub:', n_batch-n)
    Z = np.zeros((n_batch - n, length, X.shape[2]))
    X = np.vstack((X, Z))
    return X


# Get the data into proper format
# i.e [samples, timesteps, features]
# where num of samples should be integral multiple of n_batches
def getData(audio_kp, video_kp, pca, nTrainingVideo):
    # Total number of elements in each list
    # print('len(audio):', len(audio_kp), 'len(video):', len(video_kp))
    X, y = [], []  # Create the empty lists
    # Get the common keys
    keys_audio = audio_kp.keys()
    keys_video = video_kp.keys()
    keys = sorted(list(set(keys_audio).intersection(set(keys_video))))
    # print('Length of common keys:', len(keys), 'First common key:', keys[0])

    for key in tqdm(keys[0:nTrainingVideo]):
        audio = audio_kp[key]
        video = video_kp[key]
        # Get the lesser size of the two matrices
        n_lesser = len(audio) if (len(audio) < len(video)) else len(video)
        # Need to get smaller timesteps from this huge data
        segregateTimesteps = int(np.floor((n_lesser - time_delay) / length))
        # print('seg:', segregateTimesteps, 'n_lesser:', n_lesser, 'length:', length)
        # Stuff chunks of this juicy data into the x and y
        for i in range(segregateTimesteps):
            X.append(audio[i * length + time_delay:(i + 1) * length +
                           time_delay])
            y.append(video[i * length:(i + 1) * length])

    # # normalize the dataset
    # scalerX = MinMaxScaler(feature_range=(0, 1))
    # scalerY = MinMaxScaler(feature_range=(0, 1))

    # X = np.array(X)
    # X = X.reshape(X.shape[0]*X.shape[1], X.shape[2])
    # y = np.array(y)
    # y = y.reshape(y.shape[0]*y.shape[1], y.shape[2])
    # # print('Shape of X:', X.shape)

    # X = scalerX.fit_transform(X)
    # y = scalerY.fit_transform(y)

    # X = X.reshape(int(X.shape[0]/length), length, X.shape[1])
    # y = y.reshape(int(y.shape[0]/length), length, y.shape[1])

    X = batchify(X, n_batch)
    y = batchify(y, n_batch)

    n = X.shape[0]
    val_flag = False
    if n >= 5 * n_batch:  # this is where we have a validation set
        split = int(n * 0.8)
        split = int(np.ceil(split / n_batch) * n_batch)
        val_flag = True
    else:  # no validation set
        split = n

    train_X = X[0:split]
    val_X = X[split:]
    train_y = y[0:split]
    val_y = y[split:]
    return train_X, train_y, val_X, val_y, val_flag


def preparekpForPrediction(audio_kp):
    # Need to get smaller timesteps from this huge data
    segregateTimesteps = int(
        np.floor((audio_kp.shape[0] - time_delay) / length))
    # Stuff chunks of this juicy data into the X
    X = []
    for i in range(segregateTimesteps):
        X.append(audio_kp[i * length + time_delay:(i + 1) * length +
                          time_delay, :])
    X = np.array(X)
    X = batchify(X, n_batch)

    return X


def audioToPrediction(filename):
    # Get audio features
    (rate, sig) = wav.read(filename)
    audio_kp = logfbank(sig, rate)
    originalNumofPts = audio_kp.shape[0]
    return preparekpForPrediction(audio_kp), originalNumofPts


def subsample(y, fps_from=100.0, fps_to=29.97):
    factor = int(np.ceil(fps_from / fps_to))
    # Subsample the points
    new_y = np.zeros(
        (int(y.shape[0] / factor), 20, 2))  # (timesteps, 20) = (500, 20x2)
    for idx in range(new_y.shape[0]):
        if not (idx * factor > y.shape[0] - 1):
            # Get into (x, y) format
            new_y[idx, :, 0] = y[idx * factor, 0:20]
            new_y[idx, :, 1] = y[idx * factor, 20:]
        else:
            break
    # print('Subsampled y:', new_y.shape)
    new_y = [np.array(each) for each in new_y.tolist()]
    # print(len(new_y))
    return new_y


# Get the data into proper format
# i.e [samples, timesteps, features]
# where num of samples should be integral multiple of n_batches
def getDataNormalized(audio_kp, video_kp, pca, nTrainingVideo):
    # Total number of elements in each list
    # print('len(audio):', len(audio_kp), 'len(video):', len(video_kp))
    X, y = np.zeros((1, 26)), np.zeros((1, 8))  # Create the empty lists
    # Get the common keys
    keys_audio = audio_kp.keys()
    keys_video = video_kp.keys()
    keys = sorted(list(set(keys_audio).intersection(set(keys_video))))
    # print('Length of common keys:', len(keys), 'First common key:', keys[0])

    for key in tqdm(keys[0:nTrainingVideo]):
        audio = audio_kp[key]
        video = video_kp[key]
        # Get the lesser size of the two matrices
        n_lesser = len(audio) if (len(audio) < len(video)) else len(video)

    # print('audio shape:', audio.shape)


''''''
inputFolder = 'TestVideo/'
outputFolderKp = 'a2key_data/'
saveFilename = outputFolderKp + 'kp_test.pickle'
outputFolderForpix2pix = 'pix2pix_input/'
inputToA2KeyModel = 'a2key_data/images'
# print("something")
# cmd = 'rm -rf ' + inputFolder + '*-square-x-100.jpg'
# subprocess.call(cmd, shell=True)

if not (os.path.exists(outputFolderForpix2pix)):
    # Create directory
    subprocess.call('mkdir -p ' + outputFolderForpix2pix, shell=True)
if not (os.path.exists(outputFolderKp)):
    # Create directory
    subprocess.call('mkdir -p ' + outputFolderKp, shell=True)
if not (os.path.exists(inputToA2KeyModel)):
    # Create directory
    subprocess.call('mkdir -p ' + inputToA2KeyModel, shell=True)
# print("something")
searchNames = f'{inputFolder}*.jpeg'
# print(searchNames)
filenames = sorted(glob(searchNames))
# the facial landmark predictor
detector = dlib.get_frontal_face_detector()
# predictor = dlib.shape_predictor(args["shape_predictor"])
predictor = dlib.shape_predictor("data/shape_predictor_68_face_landmarks.dat")
d = []
for file in tqdm(filenames):
    img = cv2.imread(file)
    x = int(np.floor((img.shape[1] - 256) / 2))

    # Crop to a square image
    crop_img = img
    # initialize dlib's face detector (HOG-based) and then create
    image = imutils.resize(img, width=500)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # detect faces in the grayscale image
    rects = detector(gray, 1)
    if len(rects) == 1:
        tlx, tly, brx, bry = 0, 0, 0, 0
        cx, cy = 1, 1
        # loop over the face detections
        for (i, rect) in enumerate(rects):
            # determine the facial landmarks for the face region, then
            # convert the facial landmark (x, y)-coordinates to a NumPy
            # array
            shape = predictor(gray, rect)
            shape = face_utils.shape_to_np(shape)

            # convert dlib's rectangle to a OpenCV-style bounding box
            # [i.e., (x, y, w, h)], then draw the face bounding box
            (x, y, w, h) = face_utils.rect_to_bb(rect)
            # cv2.rectangle(image, (x, y), (x + w, y + h), (0, 255, 0), 2)

            # show the face number
            # cv2.putText(image, "Face #{}".format(i + 1), (x - 10, y - 10),
            #             cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            counter = 0

            # loop over the (x, y)-coordinates for the facial landmarks
            # and draw them on the image
            tempy = 0
            for (x, y) in shape:

                counter += 1
                if (counter == 3):
                    # tempy = y
                    tlx, tly = x, y
# if (counter == 4):
#     tlx, tly = x, int(y - (y - tempy)*0.15)
                if counter == 13:
                    brx = x
                if counter == 11:
                    bry = y
# cv2.putText(image, "{}".format(counter), (x - 10, y - 10),
#             cv2.FONT_HERSHEY_SIMPLEX, 0.25, (0, 0, 0), 1)
# cv2.circle(image, (x, y), 1, (0, 0, 255), -1)
                if counter == 31:
                    cx, cy = x, y

            counter = 0

        crop_img = image[max(0, (cy - 128)):max(256, (cy + 128)),
                         max(0, (cx - 128)):max(256, (cx + 128))]
        # crop_img = img[max(0, (cy - 128)):max(256, (cy + 128)), max(0, (cx - 128)):max(256, (cx + 128))]
        outputName = file[0:-len('.jpg')] + '-square-x-100.jpg'
        # print(outputName)
        # cv2.imwrite(outputName, crop_img)

        # extract the keypoints
        keypoints = get_facial_landmarks_img(crop_img)
        l = getKeypointFeatures(keypoints)
        if l != None:
            unit_kp, N, tilt, mean = l[0], l[1], l[2], l[3]
            kp_mouth = unit_kp[48:68]
            store_list = [kp_mouth, N, tilt, mean, unit_kp, keypoints]
            d.append(store_list)

            # create a patch based on the tilt, mean and the size of face
            mean_x, mean_y = int(mean[0]), int(mean[1])
            size = int(N / 15)
            aspect_ratio_mouth = 1.8
            # print('mean (y, x):', mean_y, mean_x, 'size:', size)

            patch_img = crop_img.copy()
            # patch = np.zeros_like(patch_img[ mean_y-size: mean_y+size, mean_x-size: mean_x+size ])
            patch_img[mean_y - size:mean_y + size, mean_x -
                      int(aspect_ratio_mouth * size):mean_x +
                      int(aspect_ratio_mouth * size)] = 0
            cv2.imwrite(
                inputToA2KeyModel + file[len(inputFolder):-len('.jpg')] +
                '.png', patch_img)

            drawLips(keypoints, patch_img)

            # Slap the other original image onto this
            patch_img = np.hstack((patch_img, crop_img))

            outputNamePatch = outputFolderForpix2pix + file[len(
                inputFolder):-len('.jpg')] + '.png'
            cv2.imwrite(outputNamePatch, patch_img)
            # cv2.rectangle(image, (tlx, tly), (brx, bry), (0, 0, 0), thickness=-1)

    else:
        pass

if not (os.path.exists(saveFilename)):
    os.makedirs(os.path.dirname(saveFilename), exist_ok=True)
    pkl.dump(d, open(saveFilename, "wb"))
else:
    # save the extracted keypoints
    with open(saveFilename, "wb") as output_file:
        pkl.dump(d, output_file)
