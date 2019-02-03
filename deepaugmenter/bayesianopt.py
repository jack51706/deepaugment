# (C) 2019 Baris Ozmen <hbaristr@gmail.com>

import os
import sys
from os.path import dirname, realpath
file_path = realpath(__file__)
dir_of_file = dirname(file_path)
parent_dir_of_file = dirname(dir_of_file)
sys.path.insert(0, parent_dir_of_file)

# Set experiment name
import datetime

now = datetime.datetime.now()
EXPERIMENT_NAME = f"{now.year}-{now.month}-{now.day}_{now.hour}-{now.minute}"

import pandas as pd
import numpy as np
import skopt
from skopt import gp_minimize

# Import machine learning libraries
import tensorflow as tf

config = tf.ConfigProto()
config.gpu_options.allow_growth = True  # tell tensorflow not to use all resources
session = tf.Session(config=config)
import keras

keras.backend.set_session(session)

import pathlib
import logging

EXPERIMENT_FOLDER_PATH = os.path.join(parent_dir_of_file, f"reports/experiments/{EXPERIMENT_NAME}")
log_path = pathlib.Path(EXPERIMENT_FOLDER_PATH)
log_path.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=(log_path / "info.log").absolute(), level=logging.DEBUG)

# import modules from DeepAugmenter
from augmenter import Augmenter
from childcnn import ChildCNN
from notebook import Notebook
notebook = Notebook(f"{EXPERIMENT_FOLDER_PATH}/notebook.csv")
from build_features import DataOp
from lib.decorators import Reporter
logger = Reporter.logger

AUG_TYPES = [
    "crop", "gaussian-blur", "rotate", "shear", "translate-x", "translate-y", "sharpen",
    "emboss", "additive-gaussian-noise", "dropout", "coarse-dropout", "gamma-contrast",
    "brighten", "invert", "fog", "clouds"
]

import click
@click.command()
@click.option("--dataset-name", type=click.STRING, default="cifar10")
@click.option("--model-name", type=click.STRING, default="wrn_40_4")
@click.option("--num-classes", type=click.INT, default=10)
@click.option("--training-set-size", type=click.INT, default=4000)
@click.option("--validation-set-size", type=click.INT, default=1000)
@click.option("--opt-iterations", type=click.INT, default=1000)
@click.option("--opt-samples", type=click.INT, default=5)
@click.option("--opt-last-n-epochs", type=click.INT, default=5)
@click.option("--opt-initial-points", type=click.INT, default=20)
@click.option("--child-epochs", type=click.INT, default=15)
@click.option("--child-first-train-epochs", type=click.INT, default=0)
@click.option("--child-batch-size", type=click.INT, default=32)
@logger(logfile_dir=EXPERIMENT_FOLDER_PATH)
def run_bayesianopt(
    dataset_name,
    model_name,
    num_classes,
    training_set_size,
    validation_set_size,
    opt_iterations,
    opt_samples,
    opt_last_n_epochs,
    opt_initial_points,
    child_epochs,
    child_first_train_epochs,
    child_batch_size,
):
    # warn user if TensorFlow does not see the GPU
    from tensorflow.python.client import device_lib
    if "GPU" not in str(device_lib.list_local_devices()):
        print("GPU not available!")
        logging.warning("GPU not available!")
    # Note: GPU not among local devices means GPU not used for sure,
    #       HOWEVER GPU among local devices does not guarantee it is used

    data, input_shape = DataOp.load(
        dataset_name, training_set_size, validation_set_size
    )

    data = DataOp.preprocess(data)

    child_model = ChildCNN(
        model_name, input_shape, child_batch_size, num_classes,
        "initial_model_weights.h5", logging
    )
    # first training
    if child_first_train_epochs>0:
        history = child_model.fit(data, epochs=child_first_train_epochs)
        notebook.record(0, ["first", 0.0,"first",0.0,0.0], 1, None, history)
    #
    child_model.model.save_weights(child_model.pre_augmentation_weights_path)
    augmenter = Augmenter()


    ####################################################################################################
    # Implementation of skopt by ask-tell design pattern
    # See https://geekyisawesome.blogspot.com/2018/07/hyperparameter-tuning-using-scikit.html
    ####################################################################################################

    opt = skopt.Optimizer(
        [
            skopt.space.Categorical(AUG_TYPES, name='aug1_type'),
            skopt.space.Real(0.0, 1.0, name='aug1_magnitude'),
            skopt.space.Categorical(AUG_TYPES, name='aug2_type'),
            skopt.space.Real(0.0, 1.0, name='aug2_magnitude'),
            skopt.space.Real(0.0, 1.0, name='portion')
        ],
        n_initial_points=opt_initial_points,
        base_estimator='RF', # Random Forest estimator
        acq_func='EI', # Expected Improvement
        acq_optimizer='auto',
        random_state=0
    )

    # skopt works with opt.ask() and opt.tell() functions
    for trial_no in range(1, opt_iterations+1):

        trial_hyperparams = opt.ask()
        #trial_hyperparams = [x.tolist() for x in trial_hyperparams]
        print(trial_hyperparams)

        augmented_data = augmenter.run(
            data["X_train"], data["y_train"],
            *trial_hyperparams
        )

        sample_costs=[]
        for sample_no in range(1,opt_samples+1):
            child_model.load_pre_augment_weights()
            # TRAIN
            history = child_model.fit(data, augmented_data, epochs=child_epochs)
            #
            mean_late_val_acc = np.mean(history["val_acc"][-opt_last_n_epochs:])
            sample_costs.append(mean_late_val_acc)
            notebook.record(trial_no, trial_hyperparams, sample_no, mean_late_val_acc, history)

        trial_cost = 1 - np.mean(sample_costs)
        notebook.save()

        print(trial_no, trial_cost, trial_hyperparams)
        logging.info(f"{str(trial_no)}, {str(trial_cost)}, {str(trial_hyperparams)}")
        opt.tell(trial_hyperparams, trial_cost)

    notebook.save()
    print("End")

if __name__ == "__main__":

    run_bayesianopt()