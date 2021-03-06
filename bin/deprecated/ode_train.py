from model import WaveletModel
from segmentation_pytorch.utils.metrics import FWAVACCMetric, MPAMetric
from segmentation_pytorch.utils.losses import PixelCELoss
from data import build_val_loader, build_train_loader, build_inference_loader
from easydict import EasyDict
import argparse
from collections import OrderedDict
import yaml
import copy
import time
import numpy as np
import torch.nn as nn
from torch.optim import lr_scheduler
import segmentation_pytorch as smp
import torch
from segmentation_pytorch.utils.losses import *
from segmentation_pytorch.models.ConcatSquash2D import *
from segmentation_pytorch.utils.helper import *
import os
import sys

if not os.getcwd() in sys.path:
    sys.path.append(os.getcwd())
# import segmentation_models_pytorch as smp


def main(args):
    num_classes = args.model.get('num_classes', 8)
    model = MSegNet(1, 8)
    # if args.data.get('wavelet', False):
    # 	model = WaveletModel(
    # 		encoder_name=args.model.arch,
    # 		# encoder_weights='imagenet',
    # 		encoder_weights=None,
    # 		activation='softmax',  # whatever, will do .max during metrics calculation
    # 		classes=num_classes)
    # else:
    # 	model = smp.Unet(
    # 		encoder_name=args.model.arch,
    # 		# encoder_weights='imagenet',
    # 		encoder_weights=None,
    # 		activation='softmax',  # whatever, will do .max during metrics calculation
    # 		classes=num_classes)
    # 	pass

    # loss = smp.utils.losses.BCEDiceLoss(eps=1., activation='softmax2d')

    # load class weight
    class_weight = None
    if hasattr(args.loss, 'class_weight'):
        if isinstance(args.loss.class_weight, list):
            class_weight = np.array(args.loss.class_weight)
            print(
                f'Loading class weights from static class list: {class_weight}')
        elif isinstance(args.loss.class_weight, str):
            try:
                class_weight = np.load(args.loss.class_weight)
                class_weight = np.divide(
                    1.0, class_weight, where=class_weight != 0)
                class_weight /= np.sum(class_weight)
                print(
                    f'Loading class weights from file {args.loss.class_weight}')
            except OSError as e:
                print(f'Error cannot open class weight file, {e}, exiting')
                exit(-1)

    # loss = extreme_loss(logpx, yprob_dsr_t)
    criterion = ExtremeLoss()
    metrics = [
        # MIoUMetric(num_classes=num_classes, ignore_index=0),
        MFWAVACCMetricIoUMetric(
            num_classes=num_classes, ignore_index=None),
        MPAMetric(num_classes=num_classes,
                  ignore_index=None)  # ignore background
    ]

    device = 'cuda'
    lr = args.train.lr
    optimizer = torch.optim.Adam([
        {'params': model.decoder.parameters(), 'lr': lr},

        # decrease lr for encoder in order not to permute
        # pre-trained weights with large gradients on training start
        {'params': model.encoder.parameters(), 'lr': lr},
    ])
    # optimizer = torch.optim.SGD(model.parameters(), lr=args.train.lr, momentum=args.train.momentum)
    # dataset
    args.pixel_ce = True
    train_loader, _ = build_train_loader(args)
    valid_loader, _ = build_val_loader(args)

    # create epoch runners
    # it is a simple loop of iterating over dataloader`s samples
    train_epoch = smp.utils.train.TrainEpoch(
        model,
        loss=criterion,
        metrics=metrics,
        optimizer=optimizer,
        device=device,
        verbose=True,
    )

    valid_epoch = smp.utils.train.ValidEpoch(
        model,
        loss=criterion,
        metrics=metrics,
        device=device,
        verbose=True,
    )

    max_score = 0

    exp_lr_scheduler = lr_scheduler.MultiStepLR(
        optimizer, milestones=args.train.lr_iters, gamma=0.1)

    num_epochs = args.get('epochs', 200)
    test_slides = args.get('test_slides', [
                           'S1_Helios_1of3_v1270', 'NA_T4_122117_01', 'NA_T4R_122117_19', ])
    test_loader = {}
    for test_slide in test_slides:
        # print(f'inference {test_slide}')
        args.data.slide_name = test_slide
        args.eval = True
        tiff_loader, tiff_dataset, shape = build_inference_loader(args)
        test_loader[test_slide] = tiff_loader
    # '''

    # optimizer = optim.SGD(segnet.parameters(), lr=LR, momentum=0.9)
    # for epoch in range(num_epochs):
    # optimizer.zero_grad()
    # logpx = segnet(ximg_dsr_t)
    # loss.backward()
    # optimizer.step()

    # print('Epoch %3d: loss= %f' % (epoch, loss.item()))
    # if epoch % 10 == 0 or epoch == num_epochs - 1:
    # 	show_pred(logpx[0].detach().numpy())
    # 	plt.show()
    # '''
    for i in range(0, num_epochs):

        print('\nEpoch: {}  lr: {}'.format(i, optimizer.param_groups[0]['lr']))
        train_logs = train_epoch.run(train_loader)
        test_valid_log = {'miou': np.array([]), 'mpa': np.array([])}
        for test_slide in test_slides:
            valid_logs = valid_epoch.run(test_loader[test_slides])
            print(
                f'{test_slides}, miou:{valid_logs["miou"]}, mpa:{valid_logs["mpa"]}')
            test_valid_log['miou'] = np.append(
                test_valid_log['miou'], valid_logs['miou'])
            test_valid_log['mpa'] = np.append(
                test_valid_log['mpa'], valid_logs['mpa'])

        exp_lr_scheduler.step()

        # do something (save model, change lr, etc.)
        if max_score < test_valid_log['miou'].mean():
            max_score = test_valid_log['miou'].mean()  # mean
            if not os.path.isdir(args.save_path):
                os.makedirs(args.save_path)
            torch.save(
                model,
                os.path.join(
                    args.save_path,
                    f'./{max_score}.pth'))
            print(
                f'Model saved: MIOU:{test_valid_log["miou"]}, MPA:{test_valid_log["mpa"]}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Softmax classification loss")

    parser.add_argument('--seed', type=int, default=12345)
    parser.add_argument('--config', type=str)
    parser.add_argument('--local_rank', type=int, default=0)
    args = parser.parse_args()

    with open(args.config) as f:
        config = yaml.load(f)
    params = EasyDict(config)
    params.seed = args.seed
    params.local_rank = args.local_rank
    main(params)
