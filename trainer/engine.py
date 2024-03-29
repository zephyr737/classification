# -*- coding: utf-8 -*- 
"""
@Author : Chan ZiWen
@Date : 2022/10/25 16:11
File Description:

Train and eval functions used in main.py
"""
import time
from os.path import dirname, abspath
import sys
sys.path.append(dirname(abspath(__file__)))

import math
import datetime
from typing import Iterable, Optional

import torch
import torch.nn.functional as F
from timm.data import Mixup
from timm.utils import accuracy, ModelEma
import tools.utils as utils


def train_one_epoch(model: torch.nn.Module, criterion: torch.nn.CrossEntropyLoss(),
                    data_loader: Iterable, optimizer: torch.optim.Optimizer,
                    device: torch.device, epoch: int, loss_scaler, lr_scheduler, max_norm: float = 0,
                    model_ema: Optional[ModelEma] = None, mixup_fn: Optional[Mixup] = None, summary_writer = None,
                    set_training_mode=True):
    model.train(set_training_mode)
    metric_logger = utils.MetricLogger(delimiter="  ")
    metric_logger.add_meter('lr', utils.SmoothedValue(window_size=1, fmt='{value:.6f}'))
    header = 'Epoch: [{}]'.format(epoch)
    print_freq = 20

    j = 0
    for samples, targets in metric_logger.log_every(data_loader, print_freq, header):

        samples = samples.to(device, non_blocking=True)
        targets = targets.to(device, non_blocking=True)

        if mixup_fn is not None:
            samples, targets = mixup_fn(samples, targets)

        with torch.cuda.amp.autocast():
            outputs = model(samples)
            loss = criterion(outputs, targets)

        loss_value = loss.item()
        if not math.isfinite(loss_value):
            print("Loss is {}, stopping training".format(loss_value))
            sys.exit(1)

        optimizer.zero_grad()
        lr_scheduler.step(epoch)

        # this attribute is added by timm on one optimizer (adahessian)
        # is_second_order = hasattr(optimizer, 'is_second_order') and optimizer.is_second_order
        # loss_scaler(loss, optimizer, clip_grad=max_norm, parameters=model.parameters(), create_graph=is_second_order)
        loss.backward()
        optimizer.step()

        torch.cuda.synchronize()
        if model_ema is not None:
            model_ema.update(model)

        if summary_writer is not None:
            ##训练结果保存，这里是一个步长保存一次
            summary_writer.add_scalar('train/loss', loss_value, j + epoch * len(data_loader))

        metric_logger.update(loss=loss_value)
        metric_logger.update(lr=optimizer.param_groups[0]["lr"])

        j += 1

    # gather the stats from all processes
    metric_logger.synchronize_between_processes()
    print("Average stats:", metric_logger)
    return_dict = {k: meter.global_avg for k, meter in metric_logger.meters.items()}
    return return_dict


@torch.no_grad()
def evaluate(data_loader, model, device, epoch, topk=(1, 5), summary_writer=None):
    criterion = torch.nn.CrossEntropyLoss()
    metric_logger = utils.MetricLogger(delimiter="  ")
    header = "Test: "

    # switch to evaluation mode
    model.eval()
    if isinstance(topk, tuple) and len(topk) > 1:
        topk_enable = True
    else:
        topk_enable = False
        topk = (topk,)

    j = 0
    for images, target in metric_logger.log_every(data_loader, 20, header):
        images = images.to(device, non_blocking=True)
        target = target.to(device, non_blocking=True)

        # compute output
        with torch.cuda.amp.autocast():
            output = model(images)
            loss = criterion(output, target)

        accs = accuracy(output, target, topk=topk)

        batch_size = images.shape[0]
        metric_logger.update(loss=loss.item())
        metric_logger.meters['acc1'].update(accs[0].item(), n=batch_size)
        if topk_enable:
            metric_logger.meters['acc5'].update(accs[1].item(), n=batch_size)

        if summary_writer is not None:
            ##训练结果保存，这里是一个步长保存一次
            summary_writer.add_scalar('val/loss', loss.item(), j + epoch * len(data_loader))
            summary_writer.add_scalar('val/acc1', accs[0].item(), j + epoch * len(data_loader))
            if topk_enable:
                summary_writer.add_scalar('val/acc5', accs[1].item(), j + epoch * len(data_loader))

        j += 1

    # gather the stats from all processes
    metric_logger.synchronize_between_processes()
    if topk_enable:
        print('* Acc@1 {top1.global_avg:.3f} Acc@5 {top5.global_avg:.3f} loss {losses.global_avg:.3f}'
              .format(top1=metric_logger.acc1, top5=metric_logger.acc5, losses=metric_logger.loss))
    else:
        print('* Acc@1 {top1.global_avg:.3f} loss {losses.global_avg:.3f}'
              .format(top1=metric_logger.acc1, losses=metric_logger.loss))
    return_dict = {k: meter.global_avg for k, meter in metric_logger.meters.items()}
    del metric_logger
    return return_dict


