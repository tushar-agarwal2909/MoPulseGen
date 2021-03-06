# -*- coding: utf-8 -*-
"""
Created on Mon Jul  6 22:34:42 2020

@author: agarwal.270a
"""

import pickle

def load_pickled_figs(filepath):
    #open the file and get the fig object list
    with open(filepath, 'rb') as file:
        fig_list=pickle.load(file)
        
    #show the figures. Maybe automatic in IDE like spyder
    for fig in fig_list:
        fig.show()
    
    return
def save_pickled_figs(fig_list,filepath):
    with open(filepath, 'wb') as file:
        pickle.dump(fig_list,file)
if __name__=='__main__':
    filepath='./figures/fig_HREP_highHRsim.pickle'
    load_pickled_figs(filepath)