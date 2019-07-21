import torchvision
import random
from PIL import Image
import torch.nn as nn
import torch.nn.functional as F
import torch
import numpy as np
from .layers import ResidualBlock, model_ds, transform_layer, transform_up_layer, model_up




class ResidualBlock(nn.Module):
    def __init__(self, in_features=None,out_features=None,k=None):
        super(ResidualBlock, self).__init__()
        if k is None:
            k = 3
        padd = int(np.floor(k/2))
        conv_block = [  nn.ReflectionPad2d(padd),
                        nn.Conv2d(in_features, in_features, k),
                        nn.InstanceNorm2d(in_features),
                        nn.ReLU(inplace=True),
                        nn.ReflectionPad2d(padd),
                        nn.Conv2d(in_features, out_features, k),
                        nn.InstanceNorm2d(out_features)  ]

        self.conv_block = nn.Sequential(*conv_block)

    def forward(self, x):
        return x + self.conv_block(x)
    
class ConvBlock(nn.Module):
    def __init__(self, in_features, out_features):
        super(ConvBlock, self).__init__()

        conv_block = [  nn.ReflectionPad2d(1),
                        nn.Conv2d(in_features, in_features, 3),
                        nn.InstanceNorm2d(in_features),
                        nn.ReLU(inplace=True),
                        nn.ReflectionPad2d(1),
                        nn.Conv2d(in_features, out_features, 3),
                        nn.InstanceNorm2d(out_features)  ]

        self.conv_block = nn.Sequential(*conv_block)

    def forward(self, x):
        return self.conv_block(x)


class NoiseInjection(nn.Module):
    def __init__(self, channel):
        super().__init__()

        self.weight = nn.Parameter(torch.zeros(1, channel, 1, 1))
        
    def forward(self, image, mask):
#         pdb.set_trace()
        noise = torch.randn(1, 1, image.shape[2], image.shape[3]).cpu()
        mask = mask[:,:1,:,:].repeat(1,image.shape[1],1,1)
        return image + self.weight * noise * mask
    
class model_ds(nn.Module):
    def __init__(self, in_features,out_features):
        super(model_ds, self).__init__()

        conv_block = [  nn.Conv2d(in_features, out_features, 3, stride=2, padding=1),
                            nn.InstanceNorm2d(out_features),
                            nn.ReLU(inplace=True)]

        self.conv_block = nn.Sequential(*conv_block)

    def forward(self, x):
        return self.conv_block(x)  


class model_up(nn.Module):
    def __init__(self, in_features,out_features):
        super(model_up, self).__init__()

        conv_block = [  nn.ConvTranspose2d(in_features, out_features, 3, stride=2, padding=1, output_padding=1),
                        nn.InstanceNorm2d(out_features),
                        nn.ReLU(inplace=True) ]

        self.conv_block = nn.Sequential(*conv_block)

    def forward(self, x):
        return self.conv_block(x)      

def swish(x):
    return x * F.sigmoid(x)

def get_mean_var(c):
    n_batch, n_ch, h, w = c.size()

    c_view = c.view(n_batch, n_ch, h * w)
    c_mean = c_view.mean(2)

    c_mean = c_mean.view(n_batch, n_ch, 1, 1).expand_as(c)
    c_var = c_view.var(2)
    c_var = c_var.view(n_batch, n_ch, 1, 1).expand_as(c)
    # c_var = c_var * (h * w - 1) / float(h * w)  # unbiased variance

    return c_mean, c_var


    
    
class transform_layer(nn.Module):
    
    def __init__(self,in_features,out_features):
        super(transform_layer, self).__init__()
        self.channels = in_features
        

        self.convblock = ConvBlock(in_features+in_features,out_features)
        self.up_conv = nn.Conv2d(in_features*2,in_features,3,1, 1)
        self.down_conv = nn.Sequential(
            nn.Conv2d(64,in_features//4,3,1, 1),
            nn.ReLU(),
            nn.Conv2d(in_features//4,in_features//2,1,1),
            nn.ReLU(),
            nn.Conv2d(in_features//2,in_features,1,1),
            nn.ReLU()
        )  
        self.noise = NoiseInjection(in_features)
        
        
        
        self.convblock_ = ConvBlock(in_features+64,out_features)

        self.vgg_block = nn.Sequential(
            nn.Conv2d(4,16,3,1, 1),
            nn.ReLU(),
            nn.Conv2d(16,32,1,1),
            nn.ReLU(),
            nn.Conv2d(32,64,1,1),
            nn.ReLU()
        ) 
       
    def forward(self,x,mask=None,style=None,mode='D'):
#         pdb.set_trace()
        if mode=='C':
            style = F.upsample(style, size=(x.shape[2],x.shape[2]), mode='bilinear')

            style = self.vgg_block(style)
            concat = torch.cat([x,style],1)

            out = (self.convblock_(concat))
            return out, style
        else:
            mask = F.upsample(mask, size=(x.shape[2],x.shape[2]), mode='bilinear')
            x = self.noise(x,mask)
#             style = F.upsample(style, size=(x.shape[2],x.shape[2]), mode='bilinear')

            style = self.down_conv(style)
            concat = torch.cat([x,style],1)

            out = (self.convblock(concat) + style)
            return out
        
        
class transform_up_layer(nn.Module):
    
    def __init__(self,in_features,out_features,diff=False):
        super(transform_up_layer, self).__init__()
        self.channels = in_features
        
        if diff ==True:
            self.convblock = ConvBlock(in_features*2+in_features,out_features)
        else:
            self.convblock = ConvBlock(in_features*2,out_features)
        self.up_conv = nn.Sequential(
            nn.Conv2d(in_features*2,in_features,3,1, 1),
            nn.ReLU()
        )
        
    def forward(self,x,y,mode="down"):

        y = self.up_conv(y)
        concat = torch.cat([x,y],1)
        
        out = self.convblock(concat)
        
#         out = self.adain(out,style)
        
        return out

class Flatten(nn.Module):
    def forward(self, input):
        return input.view(input.size(0), -1)    

class Stage_1(nn.Module):
    def __init__(self, input_nc, output_nc, n_residual_blocks=1):
        super(Stage_1, self).__init__()
        in_features = 64
        
        self.model_input_cloth = nn.Sequential(
             nn.ReflectionPad2d(3),
                    nn.Conv2d(5, in_features, 7),
                    nn.InstanceNorm2d(in_features),
                    nn.ReLU(inplace=True) 
        )
        
        
        
        self.block128 = nn.Sequential(
                    ResidualBlock(in_features,in_features)
                    )
        self.block128_transform = transform_layer(in_features,in_features)
        

        
        self.block64 = nn.Sequential(
                model_ds(in_features,in_features*2),
                ResidualBlock(in_features*2,in_features*2)
                )
        self.block64_transform = transform_layer(in_features*2,in_features*2)
                
    
        self.block32 = nn.Sequential(
                model_ds(in_features*2,in_features*4),
                ResidualBlock(in_features*4,in_features*4)
                )
        self.block32_transform = transform_layer(in_features*4,in_features*4)
        
        self.block16 = nn.Sequential(
                model_ds(in_features*4,in_features*8),
                ResidualBlock(in_features*8,in_features*8)
                )
        self.block16_transform = transform_layer(in_features*8,in_features*8)
        self.block8 = nn.Sequential(
                model_ds(in_features*8,in_features*8),
                ResidualBlock(in_features*8,in_features*8)
                )
        self.block8_transform = transform_layer(in_features*8,in_features*8)
        self.block4 = nn.Sequential(
                model_ds(in_features*8,in_features*8),
                ResidualBlock(in_features*8,in_features*8)
                )
        self.block4_transform = transform_layer(in_features*8,in_features*8)
        
        self.blockZ = nn.Sequential(
            
        
        )

        
        self.block_up_transform = nn.Sequential(
                model_up(in_features*8,in_features*8),
                ResidualBlock(in_features*8,in_features*8),
                model_up(in_features*8,in_features*8),
                ResidualBlock(in_features*8,in_features*8)
        )
        self.block4_up = nn.Sequential(
                nn.Conv2d(in_features*8,in_features*4,3,1,1),
                ResidualBlock(in_features*4,in_features*4)
        )
        self.block4_up_transform = transform_up_layer(in_features*4,in_features*8)
        
        
        
        self.block8_up = nn.Sequential(
                model_up(in_features*8,in_features*4),
                ResidualBlock(in_features*4,in_features*4)
        )
        self.block8_up_transform = transform_up_layer(in_features*4,in_features*8)     
        
        
        self.block16_up = nn.Sequential(
                model_up(in_features*8,in_features*4),
                ResidualBlock(in_features*4,in_features*4)
        )
        self.block16_up_transform = transform_up_layer(in_features*4,in_features*8)
        
        
        self.block32_up = nn.Sequential(
                model_up(in_features*8,in_features*4),
                ResidualBlock(in_features*4,in_features*4)
        )
        self.block32_up_transform = transform_up_layer(in_features*2,in_features*4,True)
        
        self.block64_up = nn.Sequential(
                model_up(in_features*4,in_features*2),
                ResidualBlock(in_features*2,in_features*2)
        )
        self.block64_up_transform = transform_up_layer(in_features,in_features*2,True)
        
        
        self.block128_up = nn.Sequential(
                model_up(in_features*2,in_features),
                ResidualBlock(in_features,in_features)
        )
        self.block128_up_transform = transform_up_layer(in_features//2,in_features,True)
  

        
        self.model_output = nn.Sequential(
                    nn.ReflectionPad2d(3),
                    nn.Conv2d(in_features, output_nc, 7),
                    nn.Tanh()
        ) 
        
        self.flat = Flatten()
        h_dim = 8192
        z_dim = 512
        self.fc1 = nn.Linear(h_dim, z_dim)
        self.fc2 = nn.Linear(h_dim, z_dim)
        self.fc3 = nn.Linear(z_dim, h_dim)
        
        
    def reparameterize(self, mu, logvar):
        std = logvar.mul(0.5).exp_()
        # return torch.normal(mu, std)
        esp = torch.randn(*mu.size()).cpu()
        z = mu + std * esp
        return z
    
    def bottleneck(self, h):
        mu, logvar = self.fc1(h), self.fc2(h)
        z = self.reparameterize(mu, logvar)
        return z, mu, logvar

    def encoder(self,corrupt,edges):

        style = torch.cat([corrupt,edges],1)
        y = torch.cat([torch.randn(1, 1, edges.shape[2], edges.shape[3]).cpu(),style],1)
        
        y = self.model_input_cloth(y)
        
        y128 = self.block128(y)
        y128,s_128 = self.block128_transform(x=y128,style=style,mode="C")

        y64 = self.block64(y128)
        y64, s_64 = self.block64_transform(x=y64,style=style,mode="C")
  
        y32 = self.block32(y64)
        y32, s_32 = self.block32_transform(x=y32,style=style,mode="C")

        y16 = self.block16(y32)
        y16, s_16 = self.block16_transform(x=y16,style=style,mode="C")

        y8 = self.block8(y16)
        y8, s_8 = self.block8_transform(x=y8,style=style,mode="C")

        y4 = self.block4(y8)
        y4, s_4 = self.block4_transform(x=y4,style=style,mode="C")
        
#         h = self.flat(y4)
#         z, mu, logvar = self.bottleneck(h)
        
        return y4,y8,y16

    def decoder(self,y4,y8,y16):
#         pdb.set_trace()
#         y = z.unsqueeze(2).unsqueeze(3)
#         y = self.block_up_transform(y)
        y4u = self.block4_up(y4)
        y4u = self.block4_up_transform(y4u,y4)        

 
        y8u = self.block8_up(y4u)
        y8u = self.block8_up_transform(y8u,y8)        
        
        
 
        
        y16u = self.block16_up(y8u)
        y16u = self.block16_up_transform(y16u,y16)

        

        
        y32u = self.block32_up(y16u)
 
        y64u = self.block64_up(y32u)
        
        y128u = self.block128_up(y64u)
      
        
        out = self.model_output(y128u)

        return out
        
    def forward(self,corrupt,edges):
        y4,y8,y16 = self.encoder(corrupt,edges)
        out = self.decoder(y4,y8,y16)
        return out#,z,mu, logvar
    
    