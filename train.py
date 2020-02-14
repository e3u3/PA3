from models import UNet,UNet_BN, FCN_backbone
from basic_fcn import FCN
import torch.nn as nn
from dataloader import *
from torch.utils.data import DataLoader
import numpy as np
import torch.optim as optim
import time
from torchvision.models.segmentation import deeplabv3_resnet101,deeplabv3_resnet50,DeepLabV3

from tqdm import tqdm
from utils import *
from utils import load_config
from torch.utils.tensorboard import SummaryWriter
import yaml
# from tqdm import tqdm

import pdb
CLASS_PIX=[742593219,86277776,348211930,10532667,14233504,
22534680,3647030,9750011,274652891,18558778,
62168130,25954782,3507436,125749610,5053833,
4915128,4801188,1896224,8614510]

CUDA_DIX = [0,1]
class Train:
	def __init__(self,
				 config,
				 test_path = "./test.csv",
				 train_path = "./train.csv",
				 valid_path = "./val.csv"
				):

		self.batch_size = config["batch_size"]
		self.epochs = config["epochs"]
		self.num_classes = config["num_classes"]
		self.lr = config["lr"]
		self.opt_method = config["opt_method"]
		self.loss_method = config["loss_method"]
		self.save_best = config["save_best"]
		self.retrain = config["retrain"]
		GPU = config["GPU"]
		img_shape = tuple(config["img_shape"])
		model = config["model"]

		if GPU:
			self.gpus = CUDA_DIX

		else:
			self.gpus =[]
		self.record = SummaryWriter('runs/{}_{}'.format(model,time.time()))
		self.device = torch.device("cuda" if torch.cuda.is_available() and GPU else "cpu")
		self.num_gpus = len(self.gpus)

		networks = {"UNet":UNet,
					"base_fc":FCN,
					"FCN":FCN_backbone,
					"UNet_BN":UNet_BN,
					"Deeplabv3": deeplabv3_resnet50
					}
		self.model_name = model
		if model=="FCN":
			backbone = config["backbone"]
			self.save_path = "my_model_{}_{}.pt".format(model, backbone)
			self.model = networks[self.model_name](num_classes = self.num_classes,
												   backbone=backbone).to(self.device)
		else:
			self.save_path = "my_model_{}.pt".format(model)
			self.model = networks[self.model_name](num_classes = self.num_classes).to(self.device)


		if self.num_gpus > 1:
			self.model = nn.DataParallel(self.model, device_ids=self.gpus).cuda()

		transform = transforms.Compose([
			Resize((256,512)),
			RandomFlip(),
			# RandomRescale(0.8,1.2),
			# RandomCrop((256,512)),
			ToTensor(),
			Normalize(mean=[0.485, 0.456, 0.406],
					  std=[0.229, 0.224, 0.225])
		])
		test_transform = transforms.Compose([
			ToTensor(),
			Normalize(mean=[0.485, 0.456, 0.406],
					  std=[0.229, 0.224, 0.225])
		])
		self.train_dst = CityScapesDataset(train_path,transforms=transform)
		self.valid_dst = CityScapesDataset(valid_path,transforms=transform)
		self.test_dst = CityScapesDataset(test_path,transforms=test_transform)
		print("Train set {}\n"
			  "Validation set {}\n"
			  "Test set {}".format(
			len(self.train_dst),
			len(self.valid_dst),
			len(self.test_dst)))
		self.train_loader = DataLoader(self.train_dst,
									   batch_size=self.batch_size,
									   shuffle=True,drop_last=True,
									   num_workers=1)
		self.valid_loader = DataLoader(self.valid_dst,
									   batch_size=self.batch_size,
									   shuffle=True,drop_last=True,
									   num_workers=1)
		self.test_loader = DataLoader(self.test_dst,
									  batch_size=4,
									  shuffle=True,drop_last=True,
									  num_workers=1)
		if self.retrain == True:
			self.load_weights(self.save_path)



		#self.iterations = int(len(self.train_dst) / batch_size)
	def count_weight(self):
		class_count = [0]*self.num_classes
		for i, (img, target, label) in enumerate(tqdm(self.train_loader)):
			train_y = label.to(self.device)
			for c in range(self.num_classes):
				class_count[c] += torch.sum(train_y==c)
		print(class_count)

	def train_on_batch(self, verbose=True, lr_decay=True):

		# class_pix = np.sqrt(CLASS_PIX)
		# weighted = 5 * class_pix.min() / class_pix
		# weighted = torch.tensor(weighted).float().to(self.device)
		if self.opt_method == "Adam":
			optimizer = optim.Adam(self.model.parameters(), lr=self.lr)
			if lr_decay:
				lr_sheduler = optim.lr_scheduler.StepLR(optimizer, step_size=10, gamma=0.2)
		if self.loss_method == "cross-entropy":
			criterio = nn.CrossEntropyLoss(ignore_index=-1).to(self.device)
		loss_epoch = []
		valid_accs = []
		valid_ious = []
		MAX = 0
		for epoch in range(self.epochs):
			loss_itr = []
			self.model.train()
			for i, (img, target, label) in enumerate(self.train_loader):
				itr_start = time.time()
				#print(train_x.shape)
				#pdb.set_trace()
				img = img.cuda()
				train_y_one_hot = target.cuda()
				train_y = label.cuda()

				optimizer.zero_grad()
				if self.model_name =="Deeplabv3":
					output = self.model(img)["out"]
				else:
					output = self.model(img)
				# print(train_y_one_hot.shape,output.shape)
				loss = criterio(output, train_y)
				loss.backward()
				optimizer.step()
				loss_itr.append(loss.item())
				itr_end = time.time()
				if verbose:
					print("Iterations: {} \t training loss: {} \t time: {}".format(i, loss_itr[-1], itr_end - itr_start))
			loss_epoch.append(np.mean(loss_itr))
			print("*"*10)
			print("Epoch: {} \t training loss: {}".format(epoch, loss_epoch[-1]))
			if lr_decay:
				lr_sheduler.step(epoch)

			valid_acc, valid_loss, valid_iou = self.check_accuracy(self.valid_loader, get_loss=True)
			print("Epoch: {} \t valid loss: {} \t valid accuracy: {} \t valid ious: {}".format(epoch, valid_loss, valid_acc,valid_iou))

			if self.save_best:
				if valid_iou > MAX:
					print("Saving model")
					self.save_weights(self.save_path)
					MAX = valid_iou
			valid_accs.append(valid_acc)
			valid_ious.append(valid_iou)
			plot(epoch, name=self.model_name, valid_accs=valid_accs, valid_iou=valid_ious)

	def check_accuracy(self, dataloader, get_loss=True):
		accs = []
		ious = []
		losses = []
		self.model.eval()
		if self.loss_method == "cross-entropy":
			criterio = nn.CrossEntropyLoss(ignore_index=-1)
		with torch.no_grad():
			for i, data in enumerate(dataloader):
				x, y_one_hot, y = data
				x = x.to(self.device)
				y_one_hot = y_one_hot.to(self.device)
				y = y.to(self.device)
				if self.model_name =="Deeplabv3":
					out = self.model(x)["out"]
				else:
					out = self.model(x)
				loss = criterio(out, y)
				losses.append(loss.cpu().numpy())
				y_hat = torch.argmax(out, dim=1)
				y_hat_onehot = to_one_hot(y_hat, self.num_classes).to(self.device)
				b_acc = pixel_acc(y_hat, y)
				b_ious = iou2(y_hat_onehot, y_one_hot)
				accs.append(b_acc)
				ious.append(b_ious)

		ious = np.array(ious)
		if get_loss:
			return np.mean(accs), np.mean(losses), np.mean(ious[~np.isnan(ious)])
		return np.mean(accs),np.mean(ious[~np.isnan(ious)])

	def save_weights(self,path):
		print("Saving the model ...")
		torch.save(self.model.state_dict(), path)
		print("Saving Done!")

	def load_weights(self,path):
		print("Loading the parameters")
		self.model.load_state_dict(torch.load(path))
		self.model.eval()


if __name__ == "__main__":
	config = load_config("base_fc_config.yaml")
	train = Train(config)
	train.train_on_batch()



