#!/usr/bin/env python
from __future__ import print_function, division

import sys
import os
import time

import numpy as np
import theano
import theano.tensor as T

import cv2
import lasagne
from lasagne.regularization import regularize_network_params, l2, l1
from lasagne.updates import nesterov_momentum, adam
from sklearn.model_selection import train_test_split

seed = 42
np.random.seed(seed)
evaluate_print = 100
FACTOR = 4
WIDTH, HEIGHT = 80, 12

def loadNetwork():
    input_var = T.matrix('inputs')
    input_dim = WIDTH * HEIGHT * 3
    network = buildMlp(input_var, input_dim)

    with np.load('model.npz') as f:
        param_values = [f['arr_%d' % i] for i in range(len(f.files))]
    lasagne.layers.set_all_param_values(network, param_values)

    test_prediction = lasagne.layers.get_output(network, deterministic=True)
    pred_fn = theano.function([input_var], test_prediction)
    return network, pred_fn

def preprocessImage(image, width, height):
    # Equalize v channel
    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    hsv[:, :, 2] = cv2.equalizeHist(hsv[:, :, 2])
    image = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
    image = cv2.resize(image, (width, height), interpolation=cv2.INTER_LINEAR)
    # Normalize
    x = image.flatten() / 255.
    x -= 0.5
    x *= 2
    return x

def augmentData(in_folder='cropped', out_folder='augmented_dataset'):
    images = [name for name in os.listdir(in_folder) if name.split('.jpg')[0][-2:] in ['r0', 'r1', 'r2']]
    for idx, name in enumerate(images):
        r = name.split('.jpg')[0][-2:]
        cx, cy = map(int, name.split('_')[0].split('-'))
        image_path = '{}/{}'.format(in_folder, images[idx])
        image = cv2.imread(image_path)
        height, width, n_channels = image.shape
        vertical_flip = cv2.flip(image, 0)
        horizontal_flip = cv2.flip(image, 1)
        cv2.imwrite('{}/{}-{}_{}-{}.jpg'.format(out_folder, cx, cy, idx, r), image)
        cv2.imwrite('{}/{}-{}_vert_{}-{}.jpg'.format(out_folder, cx, height - cy, idx, r), vertical_flip)
        cv2.imwrite('{}/{}-{}_hori_{}-{}.jpg'.format(out_folder, width - cx, cy, idx, r), horizontal_flip)

def loadDataset(seed=42, folder='cropped', split=True):
    images = [name for name in os.listdir(folder) if name.split('.jpg')[0][-2:] in ['r0', 'r1', 'r2']]
    tmp_im = cv2.imread('{}/{}'.format(folder, images[0]))
    height, width, n_channels = tmp_im.shape
    X = np.zeros((len(images), (WIDTH)*(HEIGHT)*n_channels), dtype=np.float64)
    y = np.zeros((len(images),), dtype=np.float64)

    print("original_shape=({},{})".format(width, height))
    print("resized_shape=({},{})".format(WIDTH, HEIGHT))

    # assert width // FACTOR == WIDTH
    factor = width / WIDTH

    for idx, name in enumerate(images):
        x_center, y_center = map(int, name.split('_')[0].split('-'))
        x_center /= factor*width
        y[idx] = x_center

        image_path = '{}/{}'.format(folder, images[idx])
        im = cv2.imread(image_path)
        X[idx, :] = preprocessImage(im, WIDTH, HEIGHT)

    print(X.shape)

    if not split:
        return X, y, images, FACTOR

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.4, random_state=seed)
    X_val, X_test, y_val, y_test = train_test_split(X_test, y_test, test_size=0.5, random_state=seed)

    return X_train, y_train, X_val, y_val, X_test, y_test

def buildMlp(input_var, input_dim):
    l_in = lasagne.layers.InputLayer(shape=(None, input_dim),
                                     input_var=input_var)
    relu = lasagne.nonlinearities.rectify
    linear = lasagne.nonlinearities.linear
    # l_in_drop = lasagne.layers.DropoutLayer(l_in, p=0.1)
    l_hid1 = lasagne.layers.DenseLayer(l_in, num_units=8, nonlinearity=relu)
    l_hid2 = lasagne.layers.DenseLayer(l_hid1, num_units=4, nonlinearity=relu)

    l_out = lasagne.layers.DenseLayer(l_hid2, num_units=1, nonlinearity=linear)
    return l_out

def iterateMinibatches(inputs, targets, batchsize, shuffle=False):
    assert len(inputs) == len(targets)
    if shuffle:
        indices = np.arange(len(inputs))
        np.random.shuffle(indices)
    for start_idx in range(0, len(inputs) - batchsize + 1, batchsize):
        if shuffle:
            excerpt = indices[start_idx:start_idx + batchsize]
        else:
            excerpt = slice(start_idx, start_idx + batchsize)
        yield inputs[excerpt], targets[excerpt]


def main(folder, num_epochs=500, batchsize=10, learning_rate=0.0001):
    # Load the dataset
    print("Loading data...")
    X_train, y_train, X_val, y_val, X_test, y_test = loadDataset(folder=folder)

    input_var = T.matrix('inputs')
    target_var = T.vector('targets')

    input_dim = X_train.shape[1]
    network = buildMlp(input_var, input_dim)

    prediction = lasagne.layers.get_output(network)
    loss = lasagne.objectives.squared_error(prediction, target_var)
    loss = loss.mean()
    loss += 1e-4 * regularize_network_params(network, l2)

    params = lasagne.layers.get_all_params(network, trainable=True)
    # updates = nesterov_momentum(loss, params, learning_rate=0.0001, momentum=0.8)
    updates = adam(loss, params, learning_rate=learning_rate)

    test_prediction = lasagne.layers.get_output(network, deterministic=True)
    test_loss = lasagne.objectives.squared_error(test_prediction, target_var)
    test_loss = test_loss.mean()

    train_fn = theano.function([input_var, target_var], loss, updates=updates)

    val_fn = theano.function([input_var, target_var], test_loss)
    best_params, best_error = None, np.inf

    # Finally, launch the training loop.
    print("Starting training...")
    # We iterate over epochs:
    for epoch in range(num_epochs):
        train_err = 0
        train_batches = 0
        start_time = time.time()
        for batch in iterateMinibatches(X_train, y_train, batchsize, shuffle=True):
            inputs, targets = batch
            train_err += train_fn(inputs, targets)
            train_batches += 1

        val_err = 0
        val_batches = 0
        for batch in iterateMinibatches(X_val, y_val, batchsize, shuffle=False):
            inputs, targets = batch
            err = val_fn(inputs, targets)
            val_err += err
            val_batches += 1
        val_error = val_err / val_batches
        if val_error < best_error:
            best_error = val_error
            best_params = lasagne.layers.get_all_param_values(network)
            np.savez('model.npz', *best_params)

        if (epoch + 1) % evaluate_print == 0:
            # Then we print the results for this epoch:
            print("Epoch {} of {} took {:.3f}s".format(
                epoch + 1, num_epochs, time.time() - start_time))
            print("  training loss:\t\t{:.6f}".format(train_err / train_batches))
            print("  validation loss:\t\t{:.6f}".format(val_err / val_batches))

    # After training, we compute and print the test error:
    lasagne.layers.set_all_param_values(network, best_params)
    test_err = 0
    test_batches = 0
    for batch in iterateMinibatches(X_test, y_test, batchsize, shuffle=False):
        inputs, targets = batch
        err = val_fn(inputs, targets)
        test_err += err
        test_batches += 1
    print("Final results:")
    print("  best validation loss:\t\t{:.6f}".format(best_error))
    print("  test loss:\t\t\t{:.6f}".format(test_err / test_batches))

    # Save best model
    np.savez('model.npz', *best_params)


if __name__ == '__main__':
    main(folder='augmented_dataset', num_epochs=500, batchsize=16, learning_rate=0.0001)
