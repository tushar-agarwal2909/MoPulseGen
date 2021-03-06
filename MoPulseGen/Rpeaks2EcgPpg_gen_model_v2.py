# -*- coding: utf-8 -*-
"""
Created on Fri Feb 14 20:47:57 2020

@author: agarwal.270a
"""

import numpy as np
import glob
import matplotlib.pyplot as plt
from scipy import io
from scipy import signal as sig
from scipy.signal import detrend
#from pathlib import Path
from simulator_for_CC import Simulator
from lib.model_CondVAE import Model_CondVAE
#from lib.model_stitchGAN import Model_stitchGAN, Net_stitchGAN
import tensorflow as tf
from tensorflow.keras import layers
import datetime
import pickle

#%%
def NLL_loss(y_true,y_pred):
    L1=tf.square(y_true[:,:,0]-y_pred[:,:,0])
    sigma=tf.clip_by_value(y_pred[:,:,1],1e-9,1e20)
    L2=tf.divide(L1,2*tf.square(sigma))+tf.math.log(sigma)
    
    return tf.reduce_mean(L2,axis=-1)

def mse_metric(y_true,y_pred):
    L1=tf.square(y_true[:,:,0]-y_pred[:,:,0])
    
    return tf.reduce_mean(L1,axis=-1)

#del Rpeaks2EcgPpg_Simulator
class Rpeaks2EcgPpg_Simulator(Simulator):
    '''
    find peak_train. from there on, more or less ppg ecg
    '''
    def __init__(self,input_list,output_list,w_pk=1,w_l=0.4,P_ID='',path='./',
                 ppg_id='ppg_green',latent_size=2,ecg_by_ppg_pk=0.75):
        '''
        in/output format: list of numpy arrays. 1st channel of output is 
        w_pk and w_l defined in seconds
        '''
        super(Rpeaks2EcgPpg_Simulator,self).__init__(input_list,output_list)
        # TODO: Didn't change w_pk and w_l sizes for ECG as ECG needs smaller 
        # windows than ppg. But might need to change depending upon changing Fs
        self.w_pk=w_pk
        self.w_l=w_l
        self.P_ID=P_ID
        self.path=path
        self.ppg_id=ppg_id
        self.ecg_id='ecg'
        self.Cout_ppg=(0,1)
        self.Cout_ecg=(1,5)
        self.Fs_ppg=25
        self.Fs_ecg=100
        self.Fs_pks=100
        self.latent_size=latent_size
        self.RNN_win_len=8 #in secs
        self.win_step_size=0.25*self.RNN_win_len #in secs
        
        self.ecg_by_ppg_pk=ecg_by_ppg_pk
        # Find ppg_basis_dict as apt ppg_basis to randomly generate PPG peaks, 
        # arranged in decreasing order of prominence
        self.ppg_model_path=path+"model_weights/{}_{}_ppg_gen_model".format(
                                                                self.P_ID,
                                                                self.ppg_id)
        fname=glob.glob(self.ppg_model_path+'*.index')
        if len(fname)!=0:
            print('PPG Gen Model Exists. Loading ...')
            self.ppg_gen_model=self.load_model(self.ppg_model_path,self.w_pk,
                                               self.Fs_ppg)
            print('Done!')
            self.make_gen_plots(self.ppg_gen_model,int(self.w_pk*self.Fs_ppg))
        else:
            print('\n Learning PPG Morphology gen-model... \n')
            self.learn_ppg_model()
            
        # Find ecg_basis_dict as apt ecg_basis to randomly generate ecg peaks, 
        # arranged in decreasing order of prominence
        self.ecg_model_path=path+"model_weights/{}_{}_ecg_gen_model".format(
                                                                self.P_ID,
                                                                self.ecg_id)
        fname=glob.glob(self.ecg_model_path+'*.index')
        if len(fname)!=0:
            print('ECG Gen Model Exists. Loading ...')
            self.ecg_gen_model=self.load_model(self.ecg_model_path,
                                               self.w_pk*self.ecg_by_ppg_pk,
                                               self.Fs_ecg)
            print('Done!')
            self.make_gen_plots(self.ecg_gen_model,
                                int(self.Fs_ecg*self.w_pk*self.ecg_by_ppg_pk))
        else:
            print('\n Learning ECG Morphology gen-model... \n')
            self.learn_ecg_model()
            
        #=====================================
        #Train
        
        self.ecg_stitchGAN_path=path+"model_weights/{}_{}_ecg_stitchGAN".format(
                                                    self.P_ID,self.ecg_id)
        fname=glob.glob(self.ecg_stitchGAN_path+'*.index')
        if len(fname)==0:
            print('\n Learning ECG stitchGAN... \n')
            self.learn_ecg_stitchGAN(save_flag=True)
            del self.ecg_stitchGAN
            
        self.ppg_stitchGAN_path=path+"model_weights/{}_{}_ppg_stitchGAN".format(
                                                    self.P_ID,self.ppg_id)
        fname=glob.glob(self.ppg_stitchGAN_path+'*.index')
        if len(fname)==0:
            print('\n Learning PPG stitchGAN... \n')
            self.learn_ppg_stitchGAN(save_flag=True)
            del self.ppg_stitchGAN #delete after training
        
        #Load and Test
        self.RNN_win_len=1# Reduce RNN win_len to reduce test time wastage
        self.win_step_size=1*self.RNN_win_len
        print('ECG stitchGAN Exists. Loading ...')
        self.ecg_stitchGAN=self.load_model_stitchGAN(self.ecg_stitchGAN_path,
                            self.w_pk*self.ecg_by_ppg_pk,
                            self.Fs_ecg)
        print('Done!')
        #self.make_gen_plots(self.ecg_gen_model,int(self.w_pk*self.Fs_ecg))
            
        print('PPG stitchGAN Exists. Loading ...')
        self.ppg_stitchGAN=self.load_model_stitchGAN(self.ppg_stitchGAN_path,self.w_pk,
                                               self.Fs_ppg)
        print('Done!')
            #self.make_gen_plots(self.ppg_gen_model,int(self.w_pk*self.Fs_ppg))


            
            
    def save_model(self,model,path):
        model.save_weights(path,save_format='tf')
        return
        
    def load_model(self,path,w_pk,Fs_out):
        '''
        Load a model from the disk.
        '''
        model=self.create_model(shape_in=[(None,int(w_pk*Fs_out)),(None,1)],
                                shape_out=[(None,int(w_pk*Fs_out))],
                                latent_size=self.latent_size)
        model.load_weights(path)
        return model
     
    def load_model_stitchGAN(self,path,w_pk,Fs_out,stateful=True):
        '''
        Load a model from the disk.
        '''
        RNN_win_len=int(Fs_out*self.RNN_win_len)
        model=self.create_stitchGAN_model(shape_in=[(None,RNN_win_len,1+int(w_pk*Fs_out))],
                                shape_out=[(None,RNN_win_len,1)],stateful=stateful,
                                batch_size=1)
        model.load_weights(path)
        return model 

    def ppg_filter(self,X0,filt=True):
        '''
        Band-pass filter multi-channel PPG signal X0
        '''
        nyq=self.Fs_ppg/2
        X1 = sig.detrend(X0,type='constant',axis=0); # Subtract mean
        if filt:
            b = sig.firls(219,np.array([0,0.3,0.5,4.5,5,nyq]),
                          np.array([0,0,1,1,0,0]),np.array([10,1,1]),nyq=nyq)
            X=np.zeros(X1.shape)
            for i in range(X1.shape[1]):
                #X[:,i] = sig.convolve(X1[:,i],b,mode='same'); # filtering using convolution, mode='same' returns the 'centered signal without any delay
                X[:,i] = sig.filtfilt(b,[1],X1[:,i])
    
        else:
            X=X1
        return X
    
    def GAN_stitcher(self,X0,filt=True):
        '''
        Use GAN sticher (The Generator) to put things together
        '''
        return self.stitchGAN.predict(X0)
    
    def learn_ppg_model(self,save_flag=False):
        '''
        Regenerate the ppg_basis
        '''
        self.ppg_gen_model=self.learn_gen_model(self.input,self.output,
                                        self.Cout_ppg,Fs_in=self.Fs_pks
                                        ,Fs_out=self.Fs_ppg,
                                        w_pk=self.w_pk,w_l=self.w_l,
                                        path4model=self.ppg_model_path,
                                        save_flag=save_flag,
                                        latent_size=self.latent_size)
        return

    def learn_ppg_stitchGAN(self,save_flag=False):
        '''
        Regenerate the ppg_basis
        '''
        
        self.ppg_stitchGAN=self.learn_stitchGAN_model(self.input,self.output,
                                        self.ppg_stitchGAN_path,
                                        self.Cout_ppg,Fs_in=self.Fs_pks,
                                        Fs_out=self.Fs_ppg,
                                        w_pk=self.w_pk,w_l=self.w_l,
                                        save_flag=save_flag,EPOCHS = 300)
        return
    
    def learn_ecg_model(self,save_flag=False):
        '''
        Regenerate the ecg_basis. Note we take every 4th element only starting
        from 0 as there is only one ecg per 4 channels of green ppg here.
        Change this as the output list changes
        '''
        self.ecg_gen_model=self.learn_gen_model(self.input[::4],
                                        self.output[::4],
                                        self.Cout_ecg,Fs_in=self.Fs_pks
                                        ,Fs_out=self.Fs_ecg,
                                        w_pk=self.w_pk*self.ecg_by_ppg_pk,
                                        w_l=self.w_l*self.ecg_by_ppg_pk,
                                        path4model=self.ecg_model_path,
                                        save_flag=save_flag,
                                        latent_size=self.latent_size)
        return
    
    def learn_ecg_stitchGAN(self,save_flag=False):
        '''
        Regenerate the ecg_basis
        '''
        
        self.ecg_stitchGAN=self.learn_stitchGAN_model(self.input[::4],
                                        self.output[::4],
                                        self.ecg_stitchGAN_path,
                                        self.Cout_ecg,Fs_in=self.Fs_pks,
                                        Fs_out=self.Fs_ecg,
                                        w_pk=self.w_pk*self.ecg_by_ppg_pk,
                                        w_l=self.w_l*self.ecg_by_ppg_pk,
                                        save_flag=save_flag,EPOCHS = 400)
        return
    
    def create_model(self,shape_in,shape_out,latent_size,
                     optimizer = tf.keras.optimizers.Adam()):
        # Create an instance of the model
        #out_len=train_data[0].shape[1]
        out_len=shape_out[0][1]
        h1=int(out_len/2)
        model = Model_CondVAE(optimizer = optimizer,
                            n_units=[h1,latent_size,h1,out_len],
                            in_shape=shape_in[0][1],
                            cond_shape=shape_in[1][1])
        return model
    
    #TODO
    def create_stitchGAN_model(self,shape_in,shape_out,mode='stitch',
                     model_path='',stateful=False,batch_size=1,
                     optimizer=tf.keras.optimizers.Adam()):
        # Create an instance of the model
        #out_len=train_data[0].shape[1]
        #out_len=shape_out[0][1]
        #h1=int(out_len/2)
        
        #del Net_stitchGAN,Model_stitchGAN
        #net= Net_stitchGAN(in_shape=shape_in[0],out_shape=shape_out[0])
        #model = Model_stitchGAN(net=net,model_path=model_path,mode=mode) 
        if stateful:
            print('\n Creating Stateful LSTM in inference Mode. \n')
            inputs = tf.keras.Input(batch_shape=[batch_size]+
                                    list(shape_in[0][1:]))
        else:
            inputs = tf.keras.Input(shape=shape_in[0][1:])
        lstm_out=layers.GRU(64,return_sequences=True,
                             stateful=stateful)(inputs)
        #out= layers.Conv1D(8,1)(lstm_out)
        #out=layers.BatchNormalization()(lstm_out)
        #out=tf.keras.activations.relu(out)
        out=layers.Conv1D(1,1)(lstm_out)
        
        #inputs = tf.keras.Input(shape=shape_in[0][1:])
        #gru_out=layers.GRU(64,return_sequences=True)(inputs)
        #gru_out=layers.Dropout(0.3,noise_shape=(None, 1, 64))(gru_out)
        #out= layers.Conv1D(1,1)(gru_out)
        
        model= tf.keras.Model(inputs=inputs,outputs=out)
        loss= tf.keras.losses.MeanSquaredError()
        metrics= ['mse']
        model.compile(loss=loss,
                      optimizer=optimizer,
                      metrics=metrics)
        model.summary()
        print(model.optimizer)
        #tf.keras.utils.plot_model(model, "simple_stitcher.png")
        return model
    
    
    def learn_gen_model(self,input_list,output_list,Cout,Fs_in,Fs_out,w_pk,
                           w_l,path4model,save_flag=True,make_plots=True,
                           latent_size=2,EPOCHS = 200):
        '''
        Learn a generative model
        
        '''
        # Convert window lenghts to n_samples of output
        w_pk=int(w_pk*Fs_out)
        w_l=int(w_l*Fs_out)
        factr=int(Fs_in/Fs_out)
        
        list_r_pk_locs=[np.arange(len(arr_pks))[arr_pks.astype(bool)] for 
                        arr_pks in input_list]
        
        #???remove terminal pk_locs for simplified clipping for now
        list_r_pk_locs=[r_pk_locs[2:-2] for r_pk_locs in list_r_pk_locs]
        
        #get nearest dsampled idx
        list_r_pk_locs_dsampled=[np.floor(r_pk_locs/factr).astype(int) for 
                                 r_pk_locs in list_r_pk_locs]
        # find RR-intervals in seconds
        list_RR_ints=[np.diff(r_pk_locs/Fs_in) for r_pk_locs in list_r_pk_locs]
        list_RR_ints=[np.concatenate([RR_ints[0:1],RR_ints]) for RR_ints in 
                      list_RR_ints] #repeat the 1st element to avoid size mismatch
         
        list_wins_clean=[self.get_windows_at_peaks(r_pk_locs,
                            y[:,Cout[0]:Cout[1]].reshape(-1),
                            w_pk=w_pk,w_l=w_l,) for r_pk_locs,y in
                            zip(list_r_pk_locs_dsampled, output_list)]
        wins_clean=np.concatenate(list_wins_clean, axis=0)
        RR_int=np.concatenate(list_RR_ints, axis=0).reshape((-1,1))
        df=np.isnan(np.sum(np.sum(wins_clean,axis=1))) #check if any nan
        df2=np.isnan(np.sum(np.sum(RR_int,axis=1))) #check if any nan
        print(df==False,df2==False)
        
        #shuffle
        perm=np.random.permutation(len(RR_int))
        RR_int,wins_clean=RR_int[perm],wins_clean[perm]
        
        #partition
        val_perc=0.14
        val_idx=int(val_perc*len(RR_int))
        val_data=[wins_clean[0:val_idx],RR_int[0:val_idx]]
        train_data=[wins_clean[val_idx:],RR_int[val_idx:]]


        def train_model(model,train_data,val_data):
            #tensorboard stuff
            current_time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            train_log_dir = 'logs/gradient_tape/' + current_time + '/train'
            test_log_dir = 'logs/gradient_tape/' + current_time + '/test'
            train_summary_writer = tf.summary.create_file_writer(train_log_dir)
            test_summary_writer = tf.summary.create_file_writer(test_log_dir)
            model.fit(data=[train_data,val_data],
                      summaries=[train_summary_writer,test_summary_writer],
                      epochs = EPOCHS)
            return model
        
        model=self.create_model([train_data[0].shape,train_data[1].shape], 
                                   [train_data[0].shape],latent_size)
        model=train_model(model,train_data,val_data)
        
        #val_data=[wins_clean[0:val_idx],RR_int[0:val_idx]]
        #x,y,mu,logsig,x_hat=model.predict(val_data)
        
        if make_plots:
            self.make_gen_plots(model,w_pk)
            #plt.close('all')
# =============================================================================
#             for idx in range(10):
#                 plt.figure()
#                 for a in range(1,5):
#                     plt.subplot(2,2,a);plt.plot(x[a*idx]);plt.plot(x_hat[a*idx])
#                     plt.title('True and synthetic sample for RR={}'.format(y[a*idx]))
#                     plt.legend(['True','Synthetic']);plt.grid(True);a+=1
# =============================================================================
        if save_flag:
            self.save_model(model,path4model)   
        return model
    
    #TODO
    def learn_stitchGAN_model(self,input_list,output_list,path4model,
                              Cout=(0,1),Fs_in=100,Fs_out=25,w_pk=1,w_l=0.4,
                              save_flag=True,make_plots=True,
                              EPOCHS = 100):
        '''
        Learn a generative model
        sim_pks2sigs
        '''

        w_pk=int(w_pk*Fs_out)
        w_l=int(w_l*Fs_out)
        factr=int(Fs_in/Fs_out)
        RNN_win_len=int(self.RNN_win_len*Fs_out)
        step_size=int(self.win_step_size*Fs_out)
        
        #RNN_win_len=800;step_size=200 #TODO: Override for debugging ECG
        GAN_input_list,GAN_output_list=[],[]
        
        for i in range(len(input_list)):
            arr_y,out,_=self.get_intermediate_signal(input_list[i],factr,
                        out=output_list[i][:,Cout[0]:Cout[1]].reshape(-1),
                        w_pk=w_pk,w_l=w_l)
            arr_y,out=self.sliding_window_fragmentation([arr_y,out],
                                RNN_win_len,step_size)
            GAN_input_list.append(arr_y)
            GAN_output_list.append(out)
        
# =============================================================================
#         for inpt,out in zip(input_list,output_list):
#             synth_sig,true_pks,_,_=self.__call__(inpt,sigs2return=[sig2stitch])
#             synth_sig,out=self.sliding_window_fragmentation(
#                             [synth_sig.reshape(-1,1),out[:,Cout[0]:Cout[1]]],
#                                 RNN_win_len,step_size)
#             GAN_input_list.append(synth_sig)
#             GAN_output_list.append(out)
# =============================================================================

        GAN_input=np.concatenate(GAN_input_list,axis=0)
        GAN_output=np.concatenate(GAN_output_list,axis=0)
        print(GAN_input.shape,GAN_output.shape)
        #partition
        val_perc=0.14
        val_idx=int(val_perc*len(GAN_input))
        val_data=[GAN_input[0:val_idx],GAN_output[0:val_idx]]
        train_data=[GAN_input[val_idx:],GAN_output[val_idx:]]
        
        #shuffle after partition as time series based
        perm=np.random.permutation(len(train_data[1]))
        train_data=[train_data[0][perm],train_data[1][perm]]
        #GAN_input,GAN_output=GAN_input[perm],GAN_output[perm]
        perm=np.random.permutation(len(val_data[1]))
        val_data=[val_data[0][perm],val_data[1][perm]]

# =============================================================================
#         def train_model(model,train_data,val_data):
#             #tensorboard stuff
#             current_time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
#             train_log_dir = 'logs/gradient_tape/' + current_time + '/train'
#             test_log_dir = 'logs/gradient_tape/' + current_time + '/test'
#             train_summary_writer = tf.summary.create_file_writer(train_log_dir)
#             test_summary_writer = tf.summary.create_file_writer(test_log_dir)
#             model.fit(data=[train_data,val_data],
#                       summaries=[train_summary_writer,test_summary_writer],
#                       epochs = EPOCHS)
#             return model
# =============================================================================
        if factr==1: #detect if ECG
            #optimizer=tf.keras.optimizers.SGD(learning_rate=0.01, momentum=0.0,
            #                                  nesterov=True)
            optimizer=tf.keras.optimizers.Adam()

        else:
            optimizer=tf.keras.optimizers.Adam()
            
        model=self.create_stitchGAN_model(shape_in=[train_data[0].shape], 
                                   shape_out=[train_data[1].shape],mode='stitch',
                                   optimizer=optimizer)
        callbacks=[]
        if save_flag:
            model_checkpoint_callback = tf.keras.callbacks.ModelCheckpoint(
                                            filepath=path4model,
                                            save_weights_only=True,
                                            monitor='val_loss',
                                            mode='min',
                                            save_best_only=True)
            callbacks.append(model_checkpoint_callback)
            
        model.fit(train_data[0],train_data[1],batch_size=64,epochs=EPOCHS,
                  validation_data=tuple(val_data),
                  callbacks=callbacks)
        #model=train_model(model,train_data,val_data)
        
        #val_data=[wins_clean[0:val_idx],RR_int[0:val_idx]]
        #x,y,mu,logsig,x_hat=model.predict(val_data)
        
        #if make_plots:
         #   self.make_stitchGAN_plots(model,w_pk)
        self.GAN_input=GAN_input
        self.GAN_output=GAN_output
        return model
    
    def make_gen_plots(self,model,w_pk):
                        
        # gen samples at a fixed RR interval
        RR = 1
        y_vec=np.array(RR).reshape((1,1))
        #y_vec=np.zeros((1,10))
        #y_vec[:,dig]=1
        
        sides = 5
        max_z = 3
        img_it = 0
        list_z_vals=np.round(np.linspace(-max_z,max_z,num=sides),1)
        plt.figure()
        for i in range(0, sides):
            z1 = list_z_vals[i] #(((i / (sides-1)) * max_z)*2) - max_z
            for j in range(0, sides):
                z2 = list_z_vals[j] #(((j / (sides-1)) * max_z)*2) - max_z
                z_ = np.array([z1, z2]).reshape((1,-1))
                decoded = model.decoder(z_,y_vec).numpy()
                plt.subplot(sides, sides, 1 + img_it)
                img_it +=1
                plt.plot(decoded.reshape(-1));plt.grid(True)
                plt.title('(z1,z2)=({},{})'.format(z1,z2))
                #plt.axis('off')
                
        #plt.subplots_adjust(left=0, bottom=0, right=1, top=1, wspace=0, hspace=.2)
        plt.suptitle('Peaks at HR={}'.format(round(60/y_vec[0,0],1)))
            
        # gen samples at different RR intervals
        sides = 5
        max_z = 3
        img_it = 0
        list_z_vals=np.round(np.linspace(-max_z,max_z,num=sides),0)
        list_RR_vals=np.round(np.linspace(0.5,1.5,num=sides),1)

        plt.figure()
        for i in range(0, sides):
            z1 = list_z_vals[i] #(((i / (sides-1)) * max_z)*2) - max_z
            z2=1*z1
            for j in range(0, sides):
                y_vec = list_RR_vals[j].reshape((1,1))
                z_ = np.array([z1, z2]).reshape((1,-1))
                decoded = model.decoder(z_,y_vec).numpy()
                plt.subplot(sides, sides, 1 + img_it)
                img_it +=1
                plt.plot(decoded.reshape(-1));plt.grid(True)
                plt.title('HR={}, z1=z2={}'.format(round(60/y_vec[0,0],1),int(z1)))
                #plt.axis('off')
        #plt.subplots_adjust(left=0, bottom=0, right=1, top=1, wspace=0, hspace=.2)
        
        n_plots=5
        list_HR_vals=np.round(np.linspace(40,120,num=n_plots),0).astype(int)
        plt.figure()
        z1,z2=0,0
        y_vec = 60/(list_HR_vals.reshape((-1,1)))
        z_ = np.array(n_plots*[z1, z2]).reshape((-1,2)).astype(np.float64)
        decoded = model.decoder(z_,y_vec).numpy()
        plt.plot(np.arange(w_pk)/w_pk,decoded.T);plt.grid(True)
        plt.xlabel('Time (s.)');plt.ylabel('Magnitude')
        plt.title('Morphology variation wrt HR')
        plt.legend(['HR={}'.format(hr) for hr in list_HR_vals])
        
        return
        
    def extract_components(self,input_list,output_list,Cout,Fs_in,Fs_out,w_pk,
                           w_l,path4basis,save_flag=False,make_plots=True):
        '''
        extract templates from output sample_data using input sample_data and 
        find a template basis using PCA
        '''
        # Convert window lenghts to n_samples of output
        w_pk=int(w_pk*Fs_out)
        w_l=int(w_l*Fs_out)
        factr=int(Fs_in/Fs_out)
        
        list_r_pk_locs=[np.arange(len(arr_pks))[arr_pks.astype(bool)] for 
                        arr_pks in input_list]
        #get nearest dsampled idx
        list_r_pk_locs_dsampled=[np.floor(r_pk_locs/factr).astype(int) for 
                                 r_pk_locs in list_r_pk_locs]
        list_wins_clean=[self.get_windows_at_peaks(r_pk_locs,
                            y[:,Cout[0]:Cout[1]].reshape(-1),
                            w_pk=w_pk,w_l=w_l,) for r_pk_locs,y in
                            zip(list_r_pk_locs_dsampled, output_list)]
        wins_clean=np.concatenate(list_wins_clean, axis=0)
        #print(list_wins_clean[0].shape,wins_clean.shape)

        mat1=self.remove_outliers(wins_clean)
        #find eig values and observe
        eigen_vals1,eigen_vecs1,avg1=self.pca(mat1) 
        
        if make_plots:
            plt.figure();plt.subplot(121);plt.plot(eigen_vals1)
            plt.subplot(122);plt.plot(avg1)
            j=0
            while j<15:
                plt.figure();k=1
                while ((k<=8) and (j+k)<len(eigen_vals1)):
                    plt.subplot(4,2,k)
                    cntr=j+k;
                    plt.plot(eigen_vecs1[:,cntr],'r')
                    plt.grid(True)
                    plt.title('eig_peak '.format(cntr))
                    k=k+1;
                j=j+8;
                
        #store basis
        basis_dict={'eig_val':eigen_vals1,'eig_vec':eigen_vecs1,'mean':avg1}
        if save_flag:
            io.savemat(path4basis,mdict=basis_dict)
            
        return basis_dict
    
    def get_intermediate_signal(self,arr_pks,factr,w_pk,w_l,out=None,model=None):
        '''
        fix w_pk, w_l requirement during prediction time.
        sim_pks2sigs
        '''
        r_pk_locs_origin=np.arange(len(arr_pks))[arr_pks.astype(bool)]
        #clip terminal r_pks
        r_pk_locs_origin=r_pk_locs_origin[1:-1]
        #get nearest dsampled idx
        r_pk_locs=np.floor(r_pk_locs_origin/factr).astype(int)
        # find RR-intervals in seconds
        RR_ints=np.diff(r_pk_locs_origin/self.Fs_pks).reshape((-1,1))
        #RR_ints=np.concatenate([RR_ints[0:1],RR_ints]).reshape((-1,1))
        
        if out is not None:
            pks=self.get_windows_at_peaks(r_pk_locs,out,w_pk=w_pk,w_l=w_l)
            pks=pks[1:] #need to ignore the first peak            
        elif model is not None:
            z_samples=np.random.normal(loc=0.,scale=1.,
                        size=(RR_ints.shape[0],self.latent_size))
            pks = model.decoder(z_samples,RR_ints).numpy()
        else:
            raise AssertionError('Supply either one of model or out args.')
            
        #print(r_pk_locs.shape,pks.shape)
        extra_feat=np.diff(r_pk_locs).reshape((-1,1))
        sig_vals=np.concatenate([extra_feat,pks],axis=1)
        #extra_feat=[np.arange(len_r-1-w_l,-1-w_l,-1) for len_r in extra_feat]
        #extra_feat=np.clip(np.concatenate(extra_feat),0,1e10)
        
        #construct dsampled arr_pks
        arr_pks_dsampled=np.zeros(int(len(arr_pks)/factr))
        arr_pks_dsampled[r_pk_locs]=1
        #construct arr_signal
        arr_y=np.zeros([int(len(arr_pks)/factr),sig_vals.shape[-1]])            
        #Place signal peaks
        for i in range(len(r_pk_locs)-1):
            arr_y[r_pk_locs[i]:r_pk_locs[i+1],:]=sig_vals[i:i+1,:]
        
        clip_indices=[r_pk_locs[0],r_pk_locs[1]]
        arr_y=arr_y[r_pk_locs[0]:r_pk_locs[-1]]        
        if out is not None:
            out=out[r_pk_locs[0]:r_pk_locs[-1]].reshape(-1,1)
            return arr_y,out
        else:
            arr_pks_dsampled=arr_pks_dsampled[r_pk_locs[0]:r_pk_locs[-1]]
            return arr_y,arr_pks_dsampled,clip_indices
    
    def __call__(self,arr_pks,sigs2return=['PPG','ECG']):
        '''
        arr_pks: Location of R-peaks determined from ECG
        k: No. of prominent basis to keep
        sim_pks2sigs
        '''
        factr_ppg=int(self.Fs_pks/self.Fs_ppg)
        factr_ecg=int(self.Fs_pks/self.Fs_ecg)
        
        RNN_win_len_ppg=int(self.RNN_win_len*self.Fs_ppg)
        step_size_ppg=RNN_win_len_ppg*1#int(self.win_step_size*self.Fs_ppg)
        RNN_win_len_ecg=int(self.RNN_win_len*self.Fs_ecg)
        step_size_ecg=RNN_win_len_ecg*1#int(self.win_step_size*self.Fs_ecg)
        
        returned_sigs=6*[None]
        if 'PPG' in sigs2return:
            w_pk_ppg=int(self.w_pk*self.Fs_ppg)
            w_l_ppg=int(self.w_l*self.Fs_ppg)
            #Get Morphologies
            arr_interm_ppg,arr_pks_ppg,clip_indices_ppg=(
                                        self.get_intermediate_signal(arr_pks,
                                        factr_ppg,
                                        model=self.ppg_gen_model,
                                        w_pk=w_pk_ppg,w_l=w_l_ppg,
                                        ))
            #arr_interm_ppg=np.expand_dims(arr_interm_ppg,axis=1)
            arr_interm_ppg=self.sliding_window_fragmentation([arr_interm_ppg],
                                            RNN_win_len_ppg,step_size_ppg)
            #Stitch them together
            arr_ppg=self.ppg_stitchGAN.predict(arr_interm_ppg)
            #arr_ppg=np.random.normal(loc=arr_ppg[:,:,0],scale=arr_ppg[:,:,1])
            #arr_ppg=truncnorm.rvs(-1,1,loc=arr_ppg[:,:,0],scale=0.2*arr_ppg[:,:,1])

            #arr_ppg_stitched=np.concatenate([arr_ppg[0,:,0],
            #                arr_ppg[1:,-step_size_ppg:,0].reshape(-1)])
            arr_ppg_stitched=arr_ppg.reshape(-1)
            #modify end clip idx to account for wastage at test time due to RNN_win_len
            clip_indices_ppg[1]=clip_indices_ppg[0]+len(arr_ppg_stitched)
            
            returned_sigs[3],returned_sigs[4],returned_sigs[5]=(
                            arr_ppg_stitched,arr_pks_ppg,clip_indices_ppg)
            
        if 'ECG' in sigs2return:
            w_pk_ecg=int(self.w_pk*self.ecg_by_ppg_pk*self.Fs_ecg)
            w_l_ecg=int(self.w_l*self.ecg_by_ppg_pk*self.Fs_ecg)
            #Get Morphologies
            arr_interm_ecg,arr_pks_ecg,clip_indices_ecg=(
                                        self.get_intermediate_signal(arr_pks,
                                        factr_ecg,
                                        model=self.ecg_gen_model,
                                        w_pk=w_pk_ecg,
                                        w_l=w_l_ecg))
            
            #arr_interm_ecg=np.expand_dims(arr_interm_ecg,axis=1)
            arr_interm_ecg=self.sliding_window_fragmentation([arr_interm_ecg],
                                            RNN_win_len_ecg,step_size_ecg)
            #Stitch them together
            arr_ecg=self.ecg_stitchGAN.predict(arr_interm_ecg)
            #arr_ecg=np.random.normal(loc=arr_ecg[:,:,0],scale=arr_ecg[:,:,1])
            #arr_ecg=truncnorm.rvs(-1,1,loc=arr_ecg[:,:,0],scale=0.2*arr_ecg[:,:,1])
            #arr_ecg_stitched=np.concatenate([arr_ecg[0,:,0],
            #                arr_ecg[1:,-step_size_ecg:,0].reshape(-1)])
            arr_ecg_stitched=arr_ecg.reshape(-1)
            #modify end clip idx to account for wastage at test time due to RNN_win_len
            clip_indices_ecg[1]=clip_indices_ecg[0]+len(arr_ecg_stitched)
            returned_sigs[0],returned_sigs[1],returned_sigs[2]=arr_ecg_stitched,arr_pks_ecg,clip_indices_ecg
        
        return returned_sigs
    
#%% Client
if __name__=='__main__':
    # Data Helpers
    import glob
    import pandas as pd
    max_ecg_val=2**16-1
    
    def get_train_data(path,val_files=[],test_files=[]):
        '''
        Use all files in the folder 'path' except the val_files and test_files
        '''
        def get_clean_ppg_and_ecg(files):
            '''
            
            '''
            list_clean_ppg=[];list_arr_pks=[];list_means=[]
            for i in range(len(files)):
                df=pd.read_csv(files[i],header=None)
                arr=df.values
                if 'clean' in files[i]:
                    #arr[:,41:45]=(detrend(arr[:,41:45].reshape(-1),0,'constant')
                     #               ).reshape((-1,4))
                    #arr[:,41:45]=(arr[:,41:45]/np.mean(arr[:,41:45].reshape(-1)
                     #               ))-1
                    #arr[:,[29,30,39,40]]-=np.mean(arr[:,[29,30,39,40]],axis=0,
                     #                      keepdims=True)
                    list_means+=[np.mean(arr[:,41:45].reshape(-1))]
                    #arr[:,41:45]-=np.ceil(max_ecg_val/2)
                    arr[:,41:45]-=list_means[-1]
                    arr[:,41:45]/=max_ecg_val
                    #arr[:,41:45]-=np.mean(arr[:,41:45].reshape(-1))
    
                    list_clean_ppg+=[np.concatenate([arr[:,29:30],arr[:,41:45]],
                                        axis=-1),arr[:,30:31],arr[:,39:40],
                                    arr[:,40:41]]
                    list_arr_pks+=4*[arr[:,45:49].reshape(-1)]  
            return list_arr_pks,list_clean_ppg,list_means
        files=glob.glob(path+'*.csv')
        #files=[fil for fil in files if 'WZ' in fil] #get wenxiao's data
        #separate val and test files
        s3=set(files);s4=set(val_files+test_files)
        files_2=list(s3.difference(s4))
        #files_2=[fil for fil in files if not((val_names[0] in fil))]
        list_arr_pks,list_clean_ppg,list_means=get_clean_ppg_and_ecg(files_2)
        return list_arr_pks,list_clean_ppg,list_means
    
    def get_test_data(file_path):
        df=pd.read_csv(file_path,header=None)
        arr=df.values
        mean=np.mean(arr[:,41:45].reshape(-1))
        #arr[:,41:45]-=np.ceil(max_ecg_val/2)
        #arr[:,41:45]-=mean
        #arr[:,41:45]/=max_ecg_val
        #arr[:,41:45]-=np.mean(arr[:,41:45].reshape(-1))
    
        #arr[:,41:45]=(arr[:,41:45]/np.mean(arr[:,41:45].reshape(-1)))-1
    
        test_out_for_check=[np.concatenate([arr[:,29:30],arr[:,41:45]],axis=-1),
                            arr[:,30:31],arr[:,39:40],arr[:,40:41]]
        test_in=arr[:,45:49].reshape(-1)
        return test_in,test_out_for_check,mean
    
    #Get Train Data for simulator
    plt.close('all')
    path_prefix= 'E:/Box Sync/' #'C:/Users/agarwal.270/Box/' #
    path=(path_prefix+'AU19/Research/PPG_ECG_proj/data/Wen_data_28_Sep/'
          'clean_lrsynced\\')
    val_files=[path+'2019092801_3154_clean.csv']
    test_files=[path+'2019092820_5701_clean.csv']
    input_list,output_list,list_means=get_train_data(path,val_files,test_files)
    
    #See ECG
    #aa=output_list[0][:,0:1].reshape(-1)
    #plt.figure();plt.plot(aa)
    
    #Create Simulator using train data
    sim_pks2sigs=Rpeaks2EcgPpg_Simulator(input_list,output_list,P_ID='W',
            path='E:/Box Sync/SP20/Research/PPG_ECG_proj/simulator_CC/data/',
            latent_size=2,w_pk=1.,w_l=0.4,ecg_by_ppg_pk=1/1)
    
# =============================================================================
#     sim_pks2sigs.ecg_stitchGAN.fit(sim_pks2sigs.GAN_input,sim_pks2sigs.GAN_output
#                                    ,batch_size=64,epochs=100)
#     
#     check_data_in=sim_pks2sigs.GAN_input
#     check_data_out=sim_pks2sigs.GAN_output
#     
#     factr_ecg=1;RNN_win_len_ecg=800;step_size_ecg=200
#     arr_interm_ecg=check_data_in[:100]
#     
#     arr_interm_ecg,arr_pks_ecg=sim_pks2sigs.get_intermediate_signal(check_data_in[:100],
#                                 factr_ecg,
#                                 model=sim_pks2sigs.ecg_gen_model,
#                                 w_pk=int(sim_pks2sigs.w_pk*sim_pks2sigs.ecg_by_ppg_pk*100),
#                                 w_l=int(sim_pks2sigs.w_l*sim_pks2sigs.ecg_by_ppg_pk*100))
#     arr_interm_ecg=sim_pks2sigs.sliding_window_fragmentation([arr_interm_ecg],
#                                     RNN_win_len_ecg,step_size_ecg)
#     
#     #Stitch them together
#     arr_ecg=sim_pks2sigs.ecg_stitchGAN.predict(arr_interm_ecg)
#     arr_ecg_stitched=np.concatenate([arr_ecg[0,:,0],
#                     arr_ecg[1:,-step_size_ecg:,0].reshape(-1)])
#     arr_true_ecg=np.concatenate([check_data_out[0,:,0],
#                     check_data_out[1:100,-step_size_ecg:,0].reshape(-1)])
#     #plt.figure();plt.plot(arr_ecg[0,:,0])
#     plt.figure();plt.plot(arr_ecg_stitched);plt.plot(arr_true_ecg,'--')
# =============================================================================

    #save models if you like the plots:
    #sim_pks2sigs.save_model(sim_pks2sigs.ppg_gen_model,sim_pks2sigs.ppg_model_path)
    #sim_pks2sigs.save_model(sim_pks2sigs.ecg_gen_model,sim_pks2sigs.ecg_model_path)
    # sim_pks2sigs.make_gen_plots(sim_pks2sigs.ecg_gen_model)
    
    #Use simulator to produce synthetic output given input
    test_in,test_out_for_check,mean=get_test_data(val_files[0])
    synth_ecg_out,test_in_ecg,clip_ecg,synth_ppg_out,test_in_ppg,clip_ppg=sim_pks2sigs(test_in)

    synth_ecg_out*=max_ecg_val #rescale
    synth_ecg_out+=mean #add back mean
    
    #Visualize
    start,end=clip_ppg
    fig1=plt.figure()
    plt.plot(test_out_for_check[0][start:end,0:1])
    plt.plot(synth_ppg_out[:])
    #plt.plot(arr_ppg[0])
    plt.plot(test_in_ppg)
    plt.legend(['True','Synthetic','R-peaks'])
    plt.grid(True)
    
    start,end=clip_ecg
    fig2=plt.figure()
    plt.plot((test_out_for_check[0][:,1:5].reshape(-1))[start:end])
    plt.plot(synth_ecg_out)
    plt.plot(test_in_ecg)
    plt.legend(['True','Synthetic','R-peaks'])
    plt.grid(True)
    
    with open('./figures/fig_REPv2.pickle', 'wb') as file:
        pickle.dump([fig1,fig2],file)