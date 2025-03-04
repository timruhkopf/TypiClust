"""
Authors: Wouter Van Gansbeke, Simon Vandenhende
Licensed under the CC BY-NC 4.0 license (https://creativecommons.org/licenses/by-nc/4.0/)
"""
import argparse
import os
import torch
import numpy as np

from utils.config import create_config
from utils.common_config import get_criterion, get_model, get_train_dataset, \
    get_val_dataset, get_train_dataloader, \
    get_val_dataloader, get_train_transformations, \
    get_val_transformations, get_optimizer, \
    adjust_learning_rate
from utils.evaluate_utils import contrastive_evaluate
from utils.memory import MemoryBank
from utils.train_utils import simclr_train
from utils.utils import fill_memory_bank
from termcolor import colored

# Parser
parser = argparse.ArgumentParser(description='SimCLR')
parser.add_argument('--config_env',
                    help='Config file for the environment')
parser.add_argument('--config_exp',
                    help='Config file for the experiment')
parser.add_argument('--seed', type=int, default=1, help='Random seed')
parser.add_argument(
    '--root_path', type=str, default='./',
    help='Root path to write into. Should be the folder that you\'d like to load ' \
         'the feature maps from for al. '
)
parser.add_argument('--epochs', type=int, default=100, help='Number of epochs')
args = parser.parse_args()


def main():
    # Retrieve config file
    p = create_config(args.config_env, args.config_exp, args.seed, root_dir=args.root_path)
    print(colored(p, 'red'))

    # Model
    print(colored('Retrieve model', 'blue'))
    model = get_model(p)
    print('Model is {}'.format(model.__class__.__name__))
    print('Model parameters: {:.2f}M'.format(sum(p.numel() for p in model.parameters()) / 1e6))
    print(model)
    model = model.cuda() if torch.cuda.is_available() else model

    # CUDNN
    print(colored('Set CuDNN benchmark', 'blue'))
    torch.backends.cudnn.benchmark = True

    # Dataset
    print(colored('Retrieve dataset', 'blue'))
    train_transforms = get_train_transformations(p)
    print('Train transforms:', train_transforms)
    val_transforms = get_val_transformations(p)
    print('Validation transforms:', val_transforms)
    train_dataset = get_train_dataset(p, train_transforms, to_augmented_dataset=True,
                                      split='train+unlabeled', root=args.root_path,
                                      download=False)  #
    # Split
    # is for stl-10
    val_dataset = get_val_dataset(p, val_transforms, root=args.root_path,
                                  download=False)
    train_dataloader = get_train_dataloader(p, train_dataset)
    val_dataloader = get_val_dataloader(p, val_dataset)
    print('Dataset contains {}/{} train/val samples'.format(len(train_dataset), len(val_dataset)))

    # Memory Bank
    print(colored('Build MemoryBank', 'blue'))
    base_dataset = get_train_dataset(p, val_transforms, split='train', root=args.root_path,
                                     download=False)  # Dataset w/o augs for
    # knn eval
    base_dataloader = get_val_dataloader(p, base_dataset, )
    memory_bank_base = MemoryBank(len(base_dataset),
                                  p['model_kwargs']['features_dim'],
                                  p['num_classes'], p['criterion_kwargs']['temperature'])
    memory_bank_base.cuda() if torch.cuda.is_available() else memory_bank_base
    memory_bank_val = MemoryBank(len(val_dataset),
                                 p['model_kwargs']['features_dim'],
                                 p['num_classes'], p['criterion_kwargs']['temperature'])
    memory_bank_val.cuda() if torch.cuda.is_available() else memory_bank_val

    # Criterion
    print(colored('Retrieve criterion', 'blue'))
    criterion = get_criterion(p)
    print('Criterion is {}'.format(criterion.__class__.__name__))
    criterion = criterion.cuda() if torch.cuda.is_available() else criterion

    # Optimizer and scheduler
    print(colored('Retrieve optimizer', 'blue'))
    optimizer = get_optimizer(p, model)
    print(optimizer)

    # Checkpoint
    if os.path.exists(p['pretext_checkpoint']):
        print(colored('Restart from checkpoint {}'.format(p['pretext_checkpoint']), 'blue'))
        checkpoint = torch.load(p['pretext_checkpoint'], map_location='cpu')
        optimizer.load_state_dict(checkpoint['optimizer'])
        model.load_state_dict(checkpoint['model'])
        model.cuda() if torch.cuda.is_available() else model
        start_epoch = checkpoint['epoch']

    else:
        print(colored('No checkpoint file at {}'.format(p['pretext_checkpoint']), 'blue'))
        start_epoch = 0
        model = model.cuda() if torch.cuda.is_available() else model

    # Training
    print(colored('Starting main loop', 'blue'))

    from utils.filelogger import BufferedFileLogger
    filelogger = BufferedFileLogger(
        file_name=f'pretext_metrics_seed_{args.seed}.csv', buffer_size=10,
        file_path=p['pretext_dir'],
        header=("metric", "value", "global_step",)
    )
    for epoch in range(start_epoch, args.epochs):
        print(colored('Epoch %d/%d' % (epoch, args.epochs), 'yellow'))
        print(colored('-' * 15, 'yellow'))

        # Adjust lr
        lr = adjust_learning_rate(p, optimizer, epoch)
        print('Adjusted learning rate to {:.5f}'.format(lr))

        # Train
        print('Train ...')
        simclr_train(train_dataloader, model, criterion, optimizer, epoch)

        # Fill memory bank
        print('Fill memory bank for kNN...')
        fill_memory_bank(base_dataloader, model, memory_bank_base)

        # Evaluate (To monitor progress - Not for validation)
        print('Evaluate ...')
        top1 = contrastive_evaluate(val_dataloader, model, memory_bank_base)
        filelogger.add_scalar('top1_knn_validation_eval', top1, epoch)
        print('Result of kNN evaluation is %.2f' % (top1))

        # Checkpoint
        print('Checkpoint ...')
        torch.save({'optimizer': optimizer.state_dict(), 'model': model.state_dict(),
                    'epoch': epoch + 1}, p['pretext_checkpoint'])

        topk = 20
        print('Mine the nearest neighbors (Top-%d)' % (topk))
        indices, acc = memory_bank_base.mine_nearest_neighbors(topk)
        np.save(p['topk_neighbors_train_path'], indices)
        np.save(p['pretext_features'], memory_bank_base.pre_lasts.cpu().numpy())
        np.save(p['pretext_features'].replace('features', 'test_features'),
                memory_bank_val.pre_lasts.cpu().numpy())

    # Save final model
    torch.save(model.state_dict(), p['pretext_model'])

    # Mine the topk nearest neighbors at the very end (Train) 
    # These will be served as input to the SCAN loss.
    print(colored('Fill memory bank for mining the nearest neighbors (train) ...', 'blue'))
    fill_memory_bank(base_dataloader, model, memory_bank_base)
    topk = 20
    print('Mine the nearest neighbors (Top-%d)' % (topk))
    indices, acc = memory_bank_base.mine_nearest_neighbors(topk)
    print('Accuracy of top-%d nearest neighbors on train set is %.2f' % (topk, 100 * acc))
    np.save(p['topk_neighbors_train_path'], indices)
    # save features
    np.save(p['pretext_features'], memory_bank_base.pre_lasts.cpu().numpy())
    np.save(p['pretext_features'].replace('features', 'test_features'),
            memory_bank_val.pre_lasts.cpu().numpy())

    # Mine the topk nearest neighbors at the very end (Val)
    # These will be used for validation.
    print(colored('Fill memory bank for mining the nearest neighbors (val) ...', 'blue'))
    fill_memory_bank(val_dataloader, model, memory_bank_val)
    topk = 5
    print('Mine the nearest neighbors (Top-%d)' % (topk))
    indices, acc = memory_bank_val.mine_nearest_neighbors(topk)
    print('Accuracy of top-%d nearest neighbors on val set is %.2f' % (topk, 100 * acc))
    np.save(p['topk_neighbors_val_path'], indices)
    filelogger.add_scalar(f'Final_top{topk}_neighbors_val_accuracy', acc, epoch)
    filelogger.close()


if __name__ == '__main__':
    main()
