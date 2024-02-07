import torch
import torchvision.transforms as transforms
from torch.optim import *
from models import *
import numpy as np

from torchvision import datasets
from torch.utils.data import DataLoader
from torch.autograd import Variable
import torch.nn.functional as F
import os
from utils import Logger, AverageMeter, accuracy, mkdir_p, savefig

from PIDAO_SI import PIDAccOptimizer_SI
from PIDAO_Sym_Tz import PIDAccOptimizer_Sym_Tz
from PIDAO_SI_AAdRMS import PIDAccOptimizer_SI_AAdRMS


transform_train = transforms.Compose([
    transforms.RandomCrop(32, padding=4),
    transforms.RandomHorizontalFlip(),
    transforms.ToTensor(),
    transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
])

transform_test = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
])

# Cifar10 Dataset
training_data = datasets.CIFAR10(root='./data',
                                 train=True,
                                 download=True,
                                 transform=transform_train
                                 )
test_data = datasets.CIFAR10(root='./data',
                             train=False,
                             download=True,
                             transform=transform_test
                             )

# Data Loader (Input Pipeline)
batch_size = 128
train_loader = DataLoader(dataset=training_data,
                          batch_size=batch_size,
                          shuffle=True,
                          num_workers=2
                          )
test_loader = DataLoader(dataset=test_data,
                         batch_size=100,
                         shuffle=False,
                         num_workers=2
                         )

classes = ('plane', 'car', 'bird', 'cat',
           'deer', 'dog', 'frog', 'horse', 'ship', 'truck')
num_epochs = 150


# Neural Network Model
class Net(nn.Module):
    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv2d(3, 6, 5)
        self.pool = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(6, 16, 5)
        self.fc1 = nn.Linear(16 * 5 * 5, 120)
        self.fc2 = nn.Linear(120, 84)
        self.fc3 = nn.Linear(84, 10)

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = torch.flatten(x, 1) # flatten all dimensions except batch
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = self.fc3(x)
        return x


# Training process
def train_loop(train_data, model, loss_fn, optimizer, epoch, device, scheduler):
    r"""
    :param train_data: train_loader
    :param model: trained model
    :param loss_fn: loss function of the model
    :param optimizer: optimizer
    :param epoch: current epoch
    :param device: computation by GPU or CPU
    :return: the training loss and accuracy under this epoch
    """
    model.train()
    train_loss_log = AverageMeter()
    train_acc_log = AverageMeter()
    for batch, (images, labels) in enumerate(train_data):
        # Convert torch tensor to Variable
        images = images.to(device)
        labels = labels.to(device)

        # Forward + Backward + Optimize
        optimizer.zero_grad()  # zero the gradient buffer
        outputs = model(images)
        train_loss = loss_fn(outputs, labels)
        train_loss.backward()
        optimizer.step()

        prec1, prec5 = accuracy(outputs.data, labels.data, topk=(1, 5))
        train_loss_log.update(train_loss.item(), images.size(0))
        train_acc_log.update(prec1.item(), images.size(0))

        if (batch + 1) % 200 == 0:
            print('Epoch [%d/%d], Step [%d/%d], Loss: %.4f, Acc: %.8f'
                  % (epoch + 1, num_epochs, batch + 1, len(train_data), train_loss_log.avg,
                     train_acc_log.avg))
    scheduler.step()

    return train_loss_log, train_acc_log


def test_loop(test_data, model, loss_fn, device):
    r"""
    :param test_data: test_loader
    :param model: trained model
    :param loss_fn: loss function of the model
    :param device: computation by GPU or CPU
    :return: the test loss and accuracy under a specific epoch
    """
    val_loss_log = AverageMeter()
    val_acc_log = AverageMeter()
    model.eval()
    for images, labels in test_data:
        images = images.to(device)
        labels = labels.to(device)
        outputs = model(images)
        test_loss = loss_fn(outputs, labels)
        val_loss_log.update(test_loss.item(), images.size(0))
        prec1, prec5 = accuracy(outputs.data, labels.data, topk=(1, 5))
        val_acc_log.update(prec1.item(), images.size(0))

    print('Accuracy of the network on the 10000 test images: %.8f %%' % val_acc_log.avg)
    print('Loss of the network on the 10000 test images: %.8f' % val_loss_log.avg)
    return val_loss_log, val_acc_log


def Optimizers(model, method_name, learning_rate):
    r"""
    :param model: trained model
    :param method_name: one specific optimizer's name for this model
    :param learning_rate: the learning_rate for optimizers
    :return: one specific optimizer for this model according with the method_name
    """
    # hyper-parameters for PIDAO-series optimizers
    lr = learning_rate
    alr = learning_rate/10  # for adaptive optimizers
    # FNN
    equivalent_momentum = 0.9
    momentum = (1 / equivalent_momentum - 1) / lr
    ki = 0.3
    kd = 1
    kp = 1*lr * (1 + momentum * lr) / lr ** 2
    
    p_ar_lr = alr
    momentum_ar = (1 / equivalent_momentum - 1) / p_ar_lr
    ki_ar = 0.1
    kd_ar = 1
    kp_ar = 1 * p_ar_lr * (1 + momentum_ar * p_ar_lr) / p_ar_lr ** 2
    # a collection of all optimizers for comparisons
    optimizers = {
        'Adam': Adam(model.parameters(), lr=alr, weight_decay=0.0001),
        'AdamW': AdamW(model.parameters(), lr=alr, weight_decay=0.0001),
        'RMSprop': RMSprop(model.parameters(), lr=alr, weight_decay=0.0001),
        'SGDM': SGD(model.parameters(), lr=lr, weight_decay=0.0001, momentum=equivalent_momentum),
        'PIDAO_Sym_Tz': PIDAccOptimizer_Sym_Tz(model.parameters(), lr=lr,
                                               weight_decay=0.0001, momentum=momentum, kp=kp, ki=ki, kd=kd),
        'PIDAO_SI': PIDAccOptimizer_SI(model.parameters(), lr=lr, weight_decay=0.0001, momentum=momentum, kp=kp, ki=ki, kd=kd),
        'PIDAO_SI_AAdRMS': PIDAccOptimizer_SI_AAdRMS(model.parameters(), lr=p_ar_lr, weight_decay=0.0001, momentum=momentum_ar, kp=kp_ar, ki=ki_ar, kd=kd_ar),
    }
    return optimizers[method_name]


def main(train_data, test_data, model, loss_fn, optimizer, num_epochs, logger, device, scheduler):
    for epoch in range(num_epochs):
        print(f"Epoch {epoch + 1}\n-------------------------------")
        train_loss_log, train_acc_log = train_loop(train_data=train_data,
                                                   model=model,
                                                   loss_fn=loss_fn,
                                                   optimizer=optimizer,
                                                   epoch=epoch,
                                                   device=device,
                                                   scheduler=scheduler)
        val_loss_log, val_acc_log = test_loop(test_data=test_data,
                                              model=model,
                                              loss_fn=loss_fn,
                                              device=device)
        logger.append([optimizer.param_groups[0]['lr'],
                       train_loss_log.avg,
                       val_loss_log.avg,
                       train_acc_log.avg,
                       val_acc_log.avg])
    logger.close()
    logger.plot()


if __name__ == '__main__':
    learning_rate = 0.01
    path = 'results/Cifar10' + '/learning_rate={0}'.format(learning_rate)
    folder = os.path.exists(path)
    if not folder:
        os.makedirs(path)

    # net's initialization
    NN_set = {'CNN': Net(),
              'ResNet18': ResNet18(),
              'ResNet34': ResNet34(),
              'VGG16': VGG('VGG16'),
              'SimpleDLA': SimpleDLA()}
    NN = 'ResNet18'
    initial_net = NN_set[NN]
    path_nn = path + '/NN={0}'.format(NN)
    if not os.path.exists(path_nn):
        os.makedirs(path_nn)
    # save the net's structure
    torch.save(initial_net, path_nn + '/initial_net.pkl')

    method = ['PIDAO_Sym_Tz', 'SGDM', 'PIDAO_SI', 'Adam', 'PIDAO_SI_AAdRMS']
    for optimizer_name in method:
        logger = Logger(path_nn + '/' + optimizer_name + '.txt')
        logger.set_names(['Learning Rate', 'Train Loss', 'Valid Loss', 'Train Acc.', 'Valid Acc.'])
        net = torch.load(path_nn + '/initial_net.pkl')
        # GPU or CPU
        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        net.to(device)
        loss = nn.CrossEntropyLoss()
        opt = Optimizers(net, optimizer_name, learning_rate)
        # scheduler = torch.optim.lr_scheduler.StepLR(optimizer=opt, step_size=100, gamma=.5)
        # scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer=opt, milestones=[50,150,200], gamma=0.5, last_epoch=-1)

        print('Here is a training process driven by the {0} optimizer'.format(optimizer_name))
        main(train_data=train_loader,
             test_data=test_loader,
             model=net,
             loss_fn=loss,
             optimizer=opt,
             num_epochs=num_epochs,
             logger=logger,
             device=device,
             scheduler=scheduler
             )