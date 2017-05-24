"""
This is a python reimplementation of the open source tracker in
High-Speed Tracking with Kernelized Correlation Filters
Joao F. Henriques, Rui Caseiro, Pedro Martins, and Jorge Batista, tPAMI 2015
modified by Di Wu
"""
import numpy as np
import matplotlib.pyplot as plt
from scipy.misc import imresize
import cv2
import time
import keras


class KMCTracker:
    def __init__(self, feature_type='raw', sub_feature_type='', sub_sub_feature_type='',
                 debug=False, gt_type='rect', load_model=False, vgglayer='',
                 model_path='./trained_models/CNN_Model_OBT100_multi_cnn_final.h5',
                 cnn_maximum=False, name_suffix="", feature_bandwidth_sigma=0.2,
                 spatial_bandwidth_sigma_factor=float(1/16.),
                 sub_sub_sub_feature_type="",
                 adaptation_rate_range_max=0.0025,
                 adaptation_rate_scale_range_max=0.005,
                 padding=2.2,
                 lambda_value=1e-4,
                 acc_time=5,
                 reg_method=0,
                 reg_min=0.1,
                 reg_mul=1e-3,
                 saliency="",
                 cross_correlation=0,
                 saliency_percent=1,
                 grabcut_mask_path='./figures/grabcut_masks/',
                 kernel='gaussian',
                 optical_flow=False):
        """
        object_example is an image showing the object to track
        feature_type:
            "raw pixels":
            "hog":
            "CNN":
        """
        # parameters according to the paper --
        self.padding = padding  # extra area surrounding the target
        self.lambda_value = lambda_value  # regularization
        self.spatial_bandwidth_sigma_factor = spatial_bandwidth_sigma_factor
        self.feature_type = feature_type
        self.patch_size = []
        self.output_sigma = []
        self.cos_window = []
        self.pos = []
        self.x = []
        self.alphaf = []
        self.xf = []
        self.yf = []
        self.im_crop = []
        self.response = []
        self.target_out = []
        self.target_sz = []
        self.vert_delta = 0
        self.horiz_delta = 0
        # OBT dataset need extra definition
        self.sub_feature_type = sub_feature_type
        self.sub_sub_feature_type = sub_sub_feature_type
        self.name = 'KCF_' + feature_type
        self.fps = -1
        self.type = gt_type
        self.res = []
        self.im_sz = []
        self.debug = debug  # a flag indicating to plot the intermediate figures
        self.first_patch_sz = []
        self.first_target_sz = []
        self.currentScaleFactor = 1
        self.load_model = load_model
        self.saliency = saliency
        self.cross_correlation = cross_correlation
        self.saliency_percent = saliency_percent
        self.grabcut_mask_path = grabcut_mask_path
        self.sub_sub_sub_feature_type = sub_sub_sub_feature_type
        self.cnn_maximum = cnn_maximum
        self.kernel = kernel

        # following is set according to Table 2:
        if self.feature_type == 'raw':
            self.adaptation_rate = 0.075  # linear interpolation factor for adaptation
            self.feature_bandwidth_sigma = 0.2
            self.cell_size = 1
        elif self.feature_type == 'hog':
            self.adaptation_rate = 0.02  # linear interpolation factor for adaptation
            self.bin_num = 31
            self.cell_size = 4
            self.feature_bandwidth_sigma = 0.5
        elif self.feature_type == 'dsst':
            # this method adopts from the paper  Martin Danelljan, Gustav Hger, Fahad Shahbaz Khan and Michael Felsberg.
            # "Accurate Scale Estimation for Robust Visual Tracking". (BMVC), 2014.
            # The project website is: http: // www.cvl.isy.liu.se / research / objrec / visualtracking / index.html
            self.adaptation_rate = 0.025  # linear interpolation factor for adaptation
            self.feature_bandwidth_sigma = 0.2
            self.cell_size = 1
            self.scale_step = 1.02
            self.nScales = 33
            self.scaleFactors = self.scale_step **(np.ceil(self.nScales * 1.0/ 2) - range(1, self.nScales+1))
            self.scale_window = np.hanning(self.nScales)
            self.scale_sigma_factor = 1./4
            self.scale_sigma = self.nScales / np.sqrt(self.nScales) * self.scale_sigma_factor
            self.ys = np.exp(-0.5 * ((range(1, self.nScales+1) - np.ceil(self.nScales * 1.0 /2))**2) / self.scale_sigma**2)
            self.ysf = np.fft.fft(self.ys)
            self.min_scale_factor = []
            self.max_scale_factor = []
            self.xs = []
            self.xsf = []
            # we use linear kernel as in the BMVC2014 paper
            self.new_sf_num = []
            self.new_sf_den = []
            self.scale_response = []
            self.lambda_scale = 1e-2
        elif self.feature_type == 'vgg' or self.feature_type == 'resnet50':
            if self.feature_type == 'vgg':
                from keras.applications.vgg19 import VGG19
                from keras.models import Model
                if vgglayer[:6]=='block2':
                    self.cell_size = 2
                elif vgglayer[:6]=='block3':
                    self.cell_size = 4
                elif vgglayer[:6] == 'block4':
                    self.cell_size = 8
                elif vgglayer[:6] == 'block5':
                    self.cell_size = 16
                else:
                    assert("not implemented")

                self.base_model = VGG19(include_top=False, weights='imagenet')
                self.extract_model = Model(input=self.base_model.input, output=self.base_model.get_layer('block3_conv4').output)
            elif self.feature_type == 'resnet50':
                from keras.applications.resnet50 import ResNet50
                from keras.models import Model
                self.base_model = ResNet50(weights='imagenet', include_top=False)
                self.extract_model = Model(input=self.base_model.input,
                                           output=self.base_model.get_layer('activation_10').output)

            self.feature_bandwidth_sigma = 1
            self.adaptation_rate = 0.01
        elif self.feature_type == 'vgg_rnn':
            from keras.applications.vgg19 import VGG19
            from keras.models import Model
            self.base_model = VGG19(include_top=False, weights='imagenet')
            self.extract_model = Model(input=self.base_model.input,
                                       output=self.base_model.get_layer('block3_conv4').output)
            # we first resize the response map to a size of 50*80 (store the resize scale)
            # because average target size is 81 *52
            self.resize_size = (240, 160)
            self.cell_size = 4
            self.response_size = [self.resize_size[0] / self.cell_size,
                                  self.resize_size[1] / self.cell_size]
            self.feature_bandwidth_sigma = 10
            self.adaptation_rate = 0.01

            grid_y = np.arange(self.response_size[0]) - np.floor(self.response_size[0] / 2)
            grid_x = np.arange(self.response_size[1]) - np.floor(self.response_size[1] / 2)

            # desired output (gaussian shaped), bandwidth proportional to target size
            self.output_sigma = np.sqrt(np.prod(self.response_size)) * self.spatial_bandwidth_sigma_factor
            rs, cs = np.meshgrid(grid_x, grid_y)
            self.y = np.exp(-0.5 / self.output_sigma ** 2 * (rs ** 2 + cs ** 2))
            self.yf = self.fft2(self.y)
            # store pre-computed cosine window
            self.cos_window = np.outer(np.hanning(self.yf.shape[0]), np.hanning(self.yf.shape[1]))
            self.path_resize_size = np.multiply(self.yf.shape, (1 + self.padding))
            self.cos_window_patch = np.outer(np.hanning(self.resize_size[0]), np.hanning(self.resize_size[1]))
            # Embedding
            if load_model:
                from keras.models import load_model
                self.lstm_model = load_model('rnn_translation_no_scale_freezconv.h5')
                self.lstm_input = np.zeros(shape=(1,10,1,60,40)).astype(float)
        elif self.feature_type == 'cnn':
            from keras.applications.vgg19 import VGG19
            from keras.models import Model
            self.base_model = VGG19(include_top=False, weights='imagenet')
            self.extract_model = Model(input=self.base_model.input,
                                       output=self.base_model.get_layer('block3_conv4').output)
            # we first resize the response map to a size of 50*80 (store the resize scale)
            # because average target size is 81 *52
            self.resize_size = (240, 160)
            self.cell_size = 4
            self.response_size = [self.resize_size[0] / self.cell_size,
                                  self.resize_size[1] / self.cell_size]
            self.feature_bandwidth_sigma = 10
            self.adaptation_rate = 0.01

            grid_y = np.arange(self.response_size[0]) - np.floor(self.response_size[0] / 2)
            grid_x = np.arange(self.response_size[1]) - np.floor(self.response_size[1] / 2)

            # desired output (gaussian shaped), bandwidth proportional to target size
            self.output_sigma = np.sqrt(np.prod(self.response_size)) * self.spatial_bandwidth_sigma_factor
            rs, cs = np.meshgrid(grid_x, grid_y)
            y = np.exp(-0.5 / self.output_sigma ** 2 * (rs ** 2 + cs ** 2))
            self.yf = self.fft2(y)
            # store pre-computed cosine window
            self.cos_window = np.outer(np.hanning(self.yf.shape[0]), np.hanning(self.yf.shape[1]))
            self.path_resize_size = np.multiply(self.yf.shape, (1 + self.padding))
            self.cos_window_patch = np.outer(np.hanning(self.resize_size[0]), np.hanning(self.resize_size[1]))
            # Embedding
            if load_model:
                from keras.models import load_model
                self.cnn_model = load_model('cnn_translation_scale_combine.h5')
        elif self.feature_type == 'multi_cnn' or self.feature_type == 'HDT':
            import keras
            from keras import backend as K
            from keras.applications.vgg19 import VGG19
            from keras.models import Model
            self.base_model = VGG19(include_top=False, weights='imagenet')
            if keras.backend._backend == 'theano':
                import theano
                self.extract_model_function = theano.function([self.base_model.input],
                                                              [self.base_model.get_layer('block1_conv2').output,
                                                               self.base_model.get_layer('block2_conv2').output,
                                                               self.base_model.get_layer('block3_conv4').output,
                                                               self.base_model.get_layer('block4_conv4').output,
                                                               self.base_model.get_layer('block5_conv4').output
                                                               ], allow_input_downcast=True)
            else:
                self.extract_model_function = K.function([self.base_model.input],
                                                         [self.base_model.get_layer('block1_conv2').output,
                                                           self.base_model.get_layer('block2_conv2').output,
                                                           self.base_model.get_layer('block3_conv4').output,
                                                           self.base_model.get_layer('block4_conv4').output,
                                                           self.base_model.get_layer('block5_conv4').output
                                                          ])

            # we first resize all the response maps to a size of 40*60 (store the resize scale)
            # because average target size is 81 *52
            self.resize_size = (240, 160)
            self.cell_size = 4
            self.response_size = [self.resize_size[0] / self.cell_size,
                                  self.resize_size[1] / self.cell_size]
            self.feature_bandwidth_sigma = feature_bandwidth_sigma
            self.adaptation_rate = adaptation_rate_range_max
            self.stability = np.ones(5)
            # store pre-computed cosine window, here is a multiscale CNN, here we have 5 layers cnn:
            self.cos_window = []
            self.y = []
            self.yf = []
            self.response_all = []
            self.max_list = []

            self.A = 0.011   #% relaxed factor
            self.feature_bandwidth_sigma = feature_bandwidth_sigma
            self.adaptation_rate = adaptation_rate_range_max
            self.loss_acc_time = 5

            for i in range(5):
                cos_wind_sz = np.divide(self.resize_size, 2**i)
                self.cos_window.append(np.outer(np.hanning(cos_wind_sz[0]), np.hanning(cos_wind_sz[1])))
                grid_y = np.arange(cos_wind_sz[0]) - np.floor(cos_wind_sz[0] / 2)
                grid_x = np.arange(cos_wind_sz[1]) - np.floor(cos_wind_sz[1] / 2)
                # desired output (gaussian shaped), bandwidth proportional to target size
                output_sigma = np.sqrt(np.prod(cos_wind_sz)) * self.spatial_bandwidth_sigma_factor
                rs, cs = np.meshgrid(grid_x, grid_y)
                y = np.exp(-0.5 / output_sigma ** 2 * (rs ** 2 + cs ** 2))
                self.y.append(y)
                self.yf.append(self.fft2(y))

            # self.path_resize_size = np.multiply(self.yf.shape, (1 + self.padding))
            # self.cos_window_patch = np.outer(np.hanning(self.resize_size[0]), np.hanning(self.resize_size[1]))
            # Embedding

            if self.sub_sub_sub_feature_type == 'maximum_res':
                self.name += '_' + self.sub_sub_sub_feature_type

            elif load_model:
                from keras.models import load_model
                if self.sub_feature_type=='class':
                    self.multi_cnn_model = load_model('./models/CNN_Model_OBT100_multi_cnn_best_valid_cnn_cifar_small_batchnormalisation_class_scale.h5')
                    from models.DataLoader import DataLoader
                    loader = DataLoader(batch_size=32, filename="./data/OBT100_new_multi_cnn%d.hdf5")
                    self.translation_value = np.asarray(loader.translation_value)
                    self.scale_value = np.asarray(loader.scale_value)
                else:
                    self.multi_cnn_model = load_model(model_path)

        if self.sub_feature_type == 'dnn_scale':
            self.scale_step = 1.01
            self.nScales = 11
            self.scaleFactors = self.scale_step ** (np.ceil(self.nScales * 1.0 / 2) - range(1, self.nScales + 1))
            # we only use the first layer as the ouput correlation
            self.ys = self.y[0]
            self.ysf = np.fft.fft2(self.ys)
            self.min_scale_factor = []
            self.max_scale_factor = []
            self.xs = []
            self.xsf = []
            self.lambda_scale = 1e-4
            self.adaptation_rate_scale = 0.005
            # to make the scale window in the range of (0.917 to 1)
            self.scale_window = np.hanning(self.nScales) + self.nScales
            self.scale_window = self.scale_window/ np.max(self.scale_window)
        if self.sub_feature_type=='dsst':
            # this method adopts from the paper  Martin Danelljan, Gustav Hger, Fahad Shahbaz Khan and Michael Felsberg.
            # "Accurate Scale Estimation for Robust Visual Tracking". (BMVC), 2014.
            # The project website is: http: // www.cvl.isy.liu.se / research / objrec / visualtracking / index.html
            self.scale_step = 1.01
            self.nScales = 33
            self.scaleFactors = self.scale_step ** (np.ceil(self.nScales * 1.0 / 2) - range(1, self.nScales + 1))
            #self.scale_window = np.hanning(self.nScales*3+1)[self.nScales:self.nScales*2]
            self.scale_window = np.hanning(self.nScales)
            self.scale_sigma_factor = 1. / 4
            self.scale_sigma = self.nScales / np.sqrt(self.nScales) * self.scale_sigma_factor
            self.ys = np.exp(
                -0.5 * ((range(1, self.nScales + 1) - np.ceil(self.nScales * 1.0 / 2)) ** 2) / self.scale_sigma ** 2)
            self.ysf = np.fft.fft(self.ys)
            self.min_scale_factor = []
            self.max_scale_factor = []
            self.xs = []
            self.xsf = []
            self.sf_num = []
            self.sf_den = []
            # we use linear kernel as in the BMVC2014 paper
            self.new_sf_num = []
            self.new_sf_den = []
            self.scale_response = []
            self.lambda_scale = 1e-2
            self.adaptation_rate_scale = adaptation_rate_scale_range_max

            if sub_sub_feature_type == 'adapted_lr' or sub_sub_feature_type == 'adapted_lr_hdt':
                self.sub_sub_feature_type = sub_sub_feature_type
                self.acc_time = acc_time
                self.loss = np.zeros(shape=(self.acc_time, 5))
                self.loss_mean = np.zeros(shape=(self.acc_time, 5))
                self.loss_std = np.zeros(shape=(self.acc_time, 5))
                self.adaptation_rate_range = [adaptation_rate_range_max, 0.0]
                self.adaptation_rate_scale_range = [adaptation_rate_scale_range_max, 0.00]
                self.adaptation_rate = self.adaptation_rate_range[0]
                self.adaptation_rate_scale = self.adaptation_rate_scale_range[0]
                self.stability = 1
                if sub_sub_feature_type == 'adapted_lr_hdt':
                    self.adaptation_rate = np.ones(shape=(5)) * self.adaptation_rate_range[0]
        if self.sub_feature_type:
            self.name += '_'+sub_feature_type
            self.feature_correlation = None
            if self.sub_sub_feature_type:
                self.name += '_' + sub_sub_feature_type
            if self.cnn_maximum:
                self.name += '_cnn_maximum'
        self.reg_method = reg_method
        self.reg_min = reg_min
        self.reg_mul = reg_mul
        self.name += name_suffix
        if self.saliency:
            self.name += '_' + self.saliency
            self.use_saliency = True
        if self.cross_correlation > 0:
            self.name += '_xcorr' + str(self.cross_correlation)
        elif self.saliency_percent < 1:
            self.name += '_' + str(self.saliency_percent)

        self.optical_flow = optical_flow
        if optical_flow:
            self.name += '_optical_flow'

        if self.kernel == 'linear':
            self.name = "DCF_" + self.feature_type

    def train(self, im, init_rect, seqname=""):
        """
        :param im: image should be of 3 dimension: M*N*C
        :param pos: the centre position of the target
        :param target_sz: target size
        """
        self.pos = [init_rect[1]+init_rect[3]/2., init_rect[0]+init_rect[2]/2.]
        self.res.append(init_rect)
        # for scaling, we always need to set it to 1
        self.currentScaleFactor = 1
        # Duh OBT is the reverse
        self.target_sz = np.asarray(init_rect[2:])
        self.target_sz = self.target_sz[::-1]
        self.first_target_sz = self.target_sz  # because we might introduce the scale changes in the detection
        # desired padded input, proportional to input target size
        self.patch_size = np.floor(self.target_sz * (1 + self.padding))
        self.first_patch_sz = np.array(self.patch_size).astype(int)   # because we might introduce the scale changes in the detection
        # desired output (gaussian shaped), bandwidth proportional to target size
        self.output_sigma = np.sqrt(np.prod(self.target_sz)) * self.spatial_bandwidth_sigma_factor
        if not self.feature_type == 'multi_cnn':
            grid_y = np.arange(np.floor(self.patch_size[0]/self.cell_size)) - np.floor(self.patch_size[0]/(2*self.cell_size))
            grid_x = np.arange(np.floor(self.patch_size[1]/self.cell_size)) - np.floor(self.patch_size[1]/(2*self.cell_size))
        if self.optical_flow:
            self.im_prev = im.transpose(1, 2, 0)
        if self.feature_type == 'resnet50':
            # this is an odd tweak to make the dimension uniform:
            if np.mod(self.patch_size[0], 2) == 0:
                grid_y = np.arange(np.floor(self.patch_size[0] / self.cell_size)-1) - np.floor(
                    self.patch_size[0] / (2 * self.cell_size)) - 0.5
            if np.mod(self.patch_size[1], 2) == 0:
                grid_x = np.arange(np.floor(self.patch_size[1] / self.cell_size)-1) - np.floor(
                    self.patch_size[1] / (2 * self.cell_size)) - 0.5

        if self.feature_type == 'vgg_rnn' or self.feature_type == 'cnn':
            grid_y = np.arange(self.response_size[0]) - np.floor(self.response_size[0]/2)
            grid_x = np.arange(self.response_size[1]) - np.floor(self.response_size[1]/2)

        if not (self.feature_type == 'multi_cnn' or self.feature_type == 'HDT'):
            rs, cs = np.meshgrid(grid_x, grid_y)
            self.y = np.exp(-0.5 / self.output_sigma ** 2 * (rs ** 2 + cs ** 2))
            self.yf = self.fft2(self.y)
            # store pre-computed cosine window
            self.cos_window = np.outer(np.hanning(self.yf.shape[0]), np.hanning(self.yf.shape[1]))

        # extract and pre-process subwindow
        if self.feature_type == 'raw' and im.shape[0] == 3:
            im = im.transpose(1, 2, 0)/255.
            self.im_sz = im.shape
        elif self.feature_type == 'dsst':
            im = im.transpose(1, 2, 0) / 255.
            self.im_sz = im.shape
            self.min_scale_factor = self.scale_step **(np.ceil(np.log(max(5. / self.patch_size)) / np.log(self.scale_step)))
            self.max_scale_factor = self.scale_step **(np.log(min(np.array(self.im_sz[:2]).astype(float) / self.target_sz)) / np.log(self.scale_step))
            self.xs = self.get_scale_sample(im, self.currentScaleFactor * self.scaleFactors)
            self.xsf = np.fft.fftn(self.xs, axes=[0])
            # we use linear kernel as in the BMVC2014 paper
            self.new_sf_num = np.multiply(self.ysf[:, None], np.conj(self.xsf))
            self.new_sf_den = np.real(np.sum(np.multiply(self.xsf, np.conj(self.xsf)), axis=1))
        elif self.feature_type == 'vgg' or self.feature_type == 'resnet50' or \
                        self.feature_type == 'vgg_rnn' or self.feature_type == 'cnn' \
                or self.feature_type == 'multi_cnn' or self.feature_type == 'HDT':
            self.im_sz = im.shape[:2]
        self.im_crop = self.get_subwindow(im, self.pos, self.patch_size)
        self.x = self.get_features()
        self.xf = []
        if self.sub_feature_type == 'grabcut':
            import matplotlib.image as mpimg
            from skimage.transform import resize
            img_grabcut = mpimg.imread(self.grabcut_mask_path+seqname+".png")
            grabcut_shape = self.x.shape[:2]
            img_grabcut = resize(img_grabcut, grabcut_shape)
            corr = np.multiply(self.x, img_grabcut[:,:,None])
            corr = np.sum(np.sum(corr, axis=0), axis=0)
            # we compute the correlation of a filter within a layer to its features
            self.feature_correlation = (corr - corr.min()) / (corr.max() - corr.min())
        if self.feature_type == 'multi_cnn' or self.feature_type == 'HDT':
            # multi_cnn will render the models to be of a list
            self.alphaf = []
            # store pre-computed cosine window, here is a multiscale CNN, here we have 5 layers cnn:
            self.W = np.asarray([0.05, 0.1, 0.2, 0.5, 1])
            self.W = self.W / np.sum(self.W)
            self.R = np.zeros(shape=(len(self.W)))
            self.loss = np.zeros(shape=(self.loss_acc_time+1, len(self.W)))
            if self.saliency == 'grabcut':
                self.feature_correlation = []
                self.feature_correlation_alpha = []
                self.feature_correlation_ranked_idx = []
                #### Note for filters especially for higher level features, there could be zero response
                # which means that those features are totally irrelavant here but will have high STD
                # we need to get rid of them!!!!!!!!!!!!!!!!!
                self.feature_valid_idx = []
            for l in range(len(self.x)):
                if np.min(self.target_sz) < 21:
                    # it the target size is too small, we don't use grabcut method
                    self.use_saliency = False
                else:
                    self.use_saliency = True
                if self.saliency == 'grabcut' and self.use_saliency:
                    import matplotlib.image as mpimg
                    from skimage.transform import resize
                    self.img_grabcut = mpimg.imread(self.grabcut_mask_path + seqname + ".png")
                    ### we find the correlation here
                    grabcut_shape = self.x[l].shape[:2]
                    img_grabcut_resized = resize(self.img_grabcut, grabcut_shape)
                    t = img_grabcut_resized
                    t_mean = t.mean()
                    t_std = t.std()
                    N = np.prod(t.shape)
                    corr_list_my = np.zeros(shape=(self.x[l].shape[2]))
                    for filter_num in range(self.x[l].shape[2]):
                        f = self.x[l][:,:,filter_num]
                        corr_list_my[filter_num] = np.multiply((f-f.mean()) / f.std(), (t-t_mean)/t_std).sum() / N
                        #corr_list_my[filter_num] = np.multiply((f - f.mean()) / f.std(), (t - t_mean) / t_std).sum() / N
                    filters = self.x[l].transpose(2,0,1)
                    #### Note for filters especially for higher level features, there could be zero response
                    # which means that those features are totally irrelavant here but will have high STD
                    # we need to get rid of them!!!!!!!!!!!!!!!!!
                    filter_std = filters.std(axis=1).std(axis=1)
                    valid_idx = filter_std != 0
                    print('layer %d, valid feature number: %d of %d'%(l, valid_idx.sum(), filters.shape[0]))
                    alpha = np.asarray([0.5 * np.log((1 - e) / e) for e in
                                        np.clip((1 - corr_list_my[valid_idx]), 10e-5, 1 - 10e-5)])
                    self.feature_valid_idx.append(valid_idx)
                    self.feature_correlation.append(corr_list_my[valid_idx])
                    self.feature_correlation_alpha.append(alpha)
                    self.feature_correlation_ranked_idx.append(np.argsort(corr_list_my[valid_idx]))
                    if False:
                        from visualisation_utils import make_mosaic
                        idx = np.argsort(self.feature_correlation[-1])
                        plt.figure(9); plt.imshow(t)
                        plt.figure(10);plt.imshow(make_mosaic(filters[valid_idx][idx[:9]], 3, 3, border=3))
                        plt.figure(11);plt.imshow(make_mosaic(filters[valid_idx][idx[-9:]], 3, 3, border=3))
                        plt.waitforbuttonpress(0)

                    self.x[l] = self.x[l][:, :, self.feature_valid_idx[l]]
                    if self.cross_correlation > 0:
                        chosen_number = np.sum(self.feature_correlation[l]>self.cross_correlation)
                        print('layer %d, xcoor: %d of %d' % (l, chosen_number, filters.shape[0]))
                        self.x[l] = self.x[l][:, :, self.feature_correlation_ranked_idx[l][-chosen_number:][::-1]]
                    elif self.saliency_percent < 1.0:
                        chosen_number = int(self.saliency_percent * len(self.feature_correlation_ranked_idx[l]))
                        self.x[l] = self.x[l][:, :, self.feature_correlation_ranked_idx[l][-chosen_number:][::-1]]
                    else:
                        self.x[l] *= self.feature_correlation[l][None, None, :]
                self.xf.append(self.fft2(self.x[l]))
                if self.kernel == 'gaussian':
                    k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, self.xf[l], self.x[l])
                    kf = self.fft2(k)
                elif self.kernel == 'linear':
                    kf = self.linear_kernel(self.xf[l])
                if self.reg_method:
                    reg_map = self.fft2(1-self.y[l] + self.reg_min)
                    reg_map = np.fft.fftshift(np.fft.fftshift(reg_map, axes=0), axes=1)
                    reg = np.multiply(reg_map, np.conj(reg_map))
                    alphaf_new = np.divide(self.yf[l], self.fft2(k) + reg * self.reg_mul)
                    self.alphaf.append(alphaf_new)
                    if False:
                        plt.figure(1)
                        alpha_real = np.real(np.multiply(alphaf_new, np.conj(alphaf_new)))
                        alpha_real = np.fft.fftshift(np.fft.fftshift(alpha_real, axes=0), axes=1)
                        print(alpha_real.max())
                        plt.imshow(alpha_real /alpha_real.max())

                        a2 = np.divide(self.yf[i], self.fft2(k) + self.lambda_value)
                        a2 = np.fft.fftshift(np.fft.fftshift(a2, axes=0), axes=1)
                        plt.figure(2)
                        a2_real = np.real(np.multiply(a2, np.conj(a2)))
                        print(a2_real.max())
                        plt.imshow(a2_real / a2_real.max())
                else:
                    self.alphaf.append(np.divide(self.yf[l], kf + self.lambda_value))
            if self.sub_feature_type == 'dsst':
                self.min_scale_factor = self.scale_step ** (np.ceil(np.log(max(5. / self.patch_size)) / np.log(self.scale_step)))
                self.max_scale_factor = self.scale_step ** (np.log(min(np.array(self.im_sz[:2]).astype(float) / self.target_sz)) / np.log(self.scale_step))
                self.xs = self.get_scale_sample(im, self.currentScaleFactor * self.scaleFactors)
                self.xsf = np.fft.fftn(self.xs, axes=[0])
                # we use linear kernel as in the BMVC2014 paper
                self.sf_num = np.multiply(self.ysf[:, None], np.conj(self.xsf))
                self.sf_den = np.real(np.sum(np.multiply(self.xsf, np.conj(self.xsf)), axis=1))
            elif self.sub_feature_type == 'dnn_scale':
                self.min_scale_factor = self.scale_step ** (np.ceil(np.log(max(5. / self.patch_size)) / np.log(self.scale_step)))
                self.max_scale_factor = self.scale_step ** (np.log(min(np.array(self.im_sz[:2]).astype(float) / self.target_sz)) / np.log(self.scale_step))
                # self.xs = self.x[0]
                # self.xsf = self.xf[0]
                # self.alpha_s_fft = self.alphaf[0]
        else:
            self.xf = self.fft2(self.x)
            if self.kernel == 'gaussian':
                k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, self.xf, self.x)
                self.alphaf = np.divide(self.yf, self.fft2(k) + self.lambda_value)
            elif self.kernel == 'linear':
                kf = self.linear_kernel(self.xf)
                self.alphaf = np.divide(self.yf, kf + self.lambda_value)

    def detect(self, im, frame):
        """
        Note: we assume the target does not change in scale, hence there is no target size
        :param im: image should be of 3 dimension: M*N*C
        :return:
        """
        # Quote from BMVC2014paper: Danelljan:
        # "In visual tracking scenarios, the scale difference between two frames is typically smaller compared to the
        # translation. Therefore, we first apply the translation filter hf given a new frame, afterwards the scale
        # filter hs is applied at the new target location.
        if self.optical_flow:
            prevgray = cv2.cvtColor(self.im_prev, cv2.COLOR_BGR2GRAY)
            gray = cv2.cvtColor(im.transpose(1, 2, 0), cv2.COLOR_BGR2GRAY)

            flow = cv2.calcOpticalFlowFarneback(prev=prevgray, next=gray,
                                                pyr_scale=.5, levels=5, winsize=15,
                                                iterations=3, poly_n=5, poly_sigma=1.2, flags=0)
            # we exclude the region of interest to eliminate the motion from the target
            flow = self.exclude_subwindow_coorindate(flow, self.pos, self.patch_size)
            mov_x_idx = np.logical_or(flow[:, :, 0] > 10e-4 , flow[:, :, 0] < -10e-4)
            mov_y_idx = np.logical_or(flow[:, :, 1] > 10e-4 , flow[:, :, 1] < -10e-4)
            x_mov = flow[mov_x_idx, 0].mean()
            y_mov = flow[mov_y_idx, 1].mean()
            #cv2.imshow('flow', self.draw_flow(gray / 255., flow))
            self.im_prev = im.transpose(1, 2, 0)
            self.pos[0] += x_mov
            self.pos[1] += y_mov
            #print('Camera moved: {0}'.format([x_mov, y_mov]))
            if False:
                plt.figure()
                plt.subplot(1,2,1); plt.imshow(self.im_prev)
                plt.subplot(1,2,2); plt.imshow(self.im_crop)
                cv2.imshow('flow', self.draw_flow(gray/255., flow))

                # motion boundaries
                flow_x_norm = (flow[:, :, 0] - flow[:, :, 0].min()) / (flow[:, :, 0].max() - flow[:, :, 0].min()) * 255
                laplacian_x = cv2.Laplacian(flow_x_norm.astype('float64'), cv2.CV_64F)
                flow_y_norm = (flow[:, :, 1] - flow[:, :, 1].min()) / (flow[:, :, 1].max() - flow[:, :, 1].min()) * 255
                laplacian_y = cv2.Laplacian(flow_y_norm.astype('float64'), cv2.CV_64F)


                plt.subplot(2, 2, 1), plt.imshow(flow_x_norm, cmap='gray')
                plt.title('Original'), plt.xticks([]), plt.yticks([])
                plt.subplot(2, 2, 2), plt.imshow(laplacian_x, cmap='gray')
                plt.title('Laplacian'), plt.xticks([]), plt.yticks([])

                laplacian_x_return = laplacian_x / 255 * (flow[:, :, 0].max() - flow[:, :, 0].min()) + flow[:, :, 0].min()
                laplacian_y_return = laplacian_y / 255 * (flow[:, :, 1].max() - flow[:, :, 1].min()) + flow[:, :, 1].min()

                laplacian = self.exclude_subwindow_coorindate(laplacian_x_return, self.pos, self.patch_size)

        # extract and pre-process subwindow
        if self.feature_type == 'raw' and im.shape[0] == 3:
            im = im.transpose(1, 2, 0)/255.

        elif self.feature_type == 'dsst':
            im = im.transpose(1, 2, 0) / 255.
            self.im_sz = im.shape

        self.im_crop = self.get_subwindow(im, self.pos, self.patch_size)

        z = self.get_features()
        if self.saliency == 'grabcut' and self.use_saliency:
            for l in range(len(z)):
                z[l] = z[l][:, :, self.feature_valid_idx[l]]
                if self.cross_correlation > 0:
                    chosen_number = np.sum(self.feature_correlation[l] > self.cross_correlation)
                    z[l] = z[l][:, :, self.feature_correlation_ranked_idx[l][-chosen_number:][::-1]]
                elif self.saliency_percent < 1.0:
                    chosen_number = int(self.saliency_percent * len(self.feature_correlation_ranked_idx[l]))
                    z[l] = z[l][:, :, self.feature_correlation_ranked_idx[l][-chosen_number:][::-1]]
                else:
                    z[l] *= self.feature_correlation[l][None, None, :]
        zf = self.fft2(z)

        if not (self.feature_type == 'multi_cnn' or self.feature_type == 'HDT'):
            if self.kernel == 'gaussian':
                k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, self.xf, self.x, zf, z)
                kf = self.fft2(k)
            elif self.kernel == 'linear':
                kf = self.linear_kernel(self.xf, zf)
            self.response = np.real(np.fft.ifft2(np.multiply(self.alphaf, kf)))
        else:
            self.response = []
            for i in range(len(z)):
                if self.kernel == 'gaussian':
                    k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, self.xf[i], self.x[i], zf[i], z[i])
                    kf = self.fft2(k)
                elif self.kernel == 'linear':
                    kf = self.linear_kernel(self.xf[i], zf[i])

                if self.feature_type == 'vgg':
                    self.response = np.real(np.fft.ifft2(np.multiply(self.alphaf[i], kf)))
                else:
                    self.response.append(np.real(np.fft.ifft2(np.multiply(self.alphaf[i], kf))))

        if self.feature_type == 'raw' or self.feature_type == 'vgg':
            # target location is at the maximum response. We must take into account the fact that, if
            # the target doesn't move, the peak will appear at the top-left corner, not at the centre
            # (this is discussed in the paper Fig. 6). The response map wrap around cyclically.
            v_centre, h_centre = np.unravel_index(self.response.argmax(), self.response.shape)
            self.vert_delta, self.horiz_delta = [v_centre - self.response.shape[0] / 2,
                                                 h_centre - self.response.shape[1] / 2]
            self.pos = self.pos + np.dot(self.cell_size, [self.vert_delta, self.horiz_delta])
        elif self.feature_type == 'vgg_rnn':
            # We need to normalise it (because our training did so):
            response = self.response
            response = (response - response.min()) / (response.max() - response.min())
            response = np.expand_dims(np.expand_dims(response, axis=0), axis=0)

            if frame <= 10:
                self.lstm_input[0, frame-1, :, :, :] = response
                predicted_output_all = self.lstm_model.predict(self.lstm_input, batch_size=1)
                predicted_output = predicted_output_all[0, frame-1,:2]
            else:
                # we always shift the frame to the left and have the final output prediction
                self.lstm_input[0, 0:9, :, :, :] = self.lstm_input[0, 1:10, :, :, :]
                self.lstm_input[0, 9, :, :, :] = response
                predicted_output_all = self.lstm_model.predict(self.lstm_input, batch_size=1)
                predicted_output = predicted_output_all[0, 9, :2]

            # target location is at the maximum response. We must take into account the fact that, if
            # the target doesn't move, the peak will appear at the top-left corner, not at the centre
            # (this is discussed in the paper Fig. 6). The response map wrap around cyclically.
            v_centre, h_centre = np.unravel_index(self.response.argmax(), self.response.shape)
            self.vert_delta, self.horiz_delta = [v_centre - self.response.shape[0] / 2,
                                                 h_centre - self.response.shape[1] / 2]
            self.pos_old = [
                self.pos[1] + self.patch_size[1] * 1.0 / self.resize_size[1] * self.horiz_delta - self.target_sz[
                    1] / 2.,
                self.pos[0] + self.patch_size[0] * 1.0 / self.resize_size[0] * self.vert_delta - self.target_sz[
                    0] / 2., ]

            self.pos = [self.pos[0] + self.target_sz[0] * predicted_output[0],
                        self.pos[1] + self.target_sz[1] * predicted_output[1]]

            self.pos = [max(self.target_sz[0] / 2, min(self.pos[0], self.im_sz[0] - self.target_sz[0] / 2)),
                        max(self.target_sz[1] / 2, min(self.pos[1], self.im_sz[1] - self.target_sz[1] / 2))]
        elif self.feature_type == 'cnn':
            # We need to normalise it (because our training did so):
            response = self.response
            response = (response-response.min())/(response.max()-response.min())
            response = np.expand_dims(np.expand_dims(response, axis=0), axis=0)
            predicted_output = self.cnn_model.predict(response, batch_size=1)

            # target location is at the maximum response. We must take into account the fact that, if
            # the target doesn't move, the peak will appear at the top-left corner, not at the centre
            # (this is discussed in the paper Fig. 6). The response map wrap around cyclically.
            v_centre, h_centre = np.unravel_index(self.response.argmax(), self.response.shape)
            self.vert_delta, self.horiz_delta = [v_centre - self.response.shape[0]/2, h_centre - self.response.shape[1]/2]
            self.pos_old = [self.pos[1] + self.patch_size[1] * 1.0 / self.resize_size[1] * self.horiz_delta - self.target_sz[1] / 2.,
                            self.pos[0] + self.patch_size[0] * 1.0 / self.resize_size[0] * self.vert_delta - self.target_sz[0] / 2.,]

            self.pos = [self.pos[0] + self.target_sz[0] * predicted_output[0][0],
                    self.pos[1] + self.target_sz[1] * predicted_output[0][1]]

            self.pos = [max(self.target_sz[0] / 2, min(self.pos[0], self.im_sz[0] - self.target_sz[0] / 2)),
                        max(self.target_sz[1] / 2, min(self.pos[1], self.im_sz[1] - self.target_sz[1] / 2))]
        elif self.feature_type == 'multi_cnn':
            response_all = np.zeros(shape=(5, self.resize_size[0], self.resize_size[1]))
            self.max_list = [np.max(x) for x in self.response]

            for i in range(len(self.response)):
                response_all[i, :, :] = imresize(self.response[i], size=self.resize_size)
                if self.sub_feature_type == 'class' or self.cnn_maximum:
                    response_all[i, :, :] = np.multiply(response_all[i, :, :], self.max_list[i])

            if self.sub_sub_sub_feature_type == 'maximum_res':
                self.response = response_all.sum(axis=0)
                self.response /= 255. * len(response_all)
                v_centre, h_centre = np.unravel_index(self.response.argmax(), self.response.shape)
                self.vert_delta, self.horiz_delta = [v_centre - self.response.shape[0] / 2.,
                                                     h_centre - self.response.shape[1] / 2.]
                self.cell_size = np.divide(self.patch_size, self.response.shape)
                self.pos = self.pos + np.multiply(self.cell_size, [self.vert_delta, self.horiz_delta])
            else:
                response_all = response_all.astype('float32') / 255. - 0.5
                self.response_all = response_all
                response_all = np.expand_dims(response_all, axis=0)
                predicted_output = self.multi_cnn_model.predict(response_all, batch_size=1)

                if self.sub_feature_type=='class':
                    translational_x = np.dot(predicted_output[0], self.translation_value)
                    translational_y = np.dot(predicted_output[1], self.translation_value)
                    scale_change = np.dot(predicted_output[2], self.scale_value)
                    # translational_x = self.translation_value[np.argmax(predicted_output[0])]
                    # translational_y = self.translation_value[np.argmax(predicted_output[1])]
                    # scale_change = self.scale_value[np.argmax(predicted_output[2])]
                    # calculate the new target size
                    self.target_sz = np.divide(self.target_sz, scale_change)
                    # we also require the target size to be smaller than the image size deivided by paddings
                    self.target_sz = [min(self.im_sz[0], self.target_sz[0]), min(self.im_sz[1], self.target_sz[1])]
                    self.patch_size = np.multiply(self.target_sz, (1 + self.padding))

                    self.vert_delta, self.horiz_delta = \
                        [self.target_sz[0] * translational_x, self.target_sz[1] * translational_y]

                    self.pos = [self.pos[0] + self.target_sz[0] * translational_x,
                                self.pos[1] + self.target_sz[1] * translational_y]
                    self.pos = [max(self.target_sz[0] / 2, min(self.pos[0], self.im_sz[0] - self.target_sz[0] / 2)),
                                max(self.target_sz[1] / 2, min(self.pos[1], self.im_sz[1] - self.target_sz[1] / 2))]
                else:
                    ##################################################################################
                    # we need to train the tracker again for scaling, it's almost the replicate of train
                    ##################################################################################
                    # target location is at the maximum response. We must take into account the fact that, if
                    # the target doesn't move, the peak will appear at the top-left corner, not at the centre
                    # (this is discussed in the paper Fig. 6). The response map wrap around cyclically.
                    self.vert_delta, self.horiz_delta = \
                        [self.target_sz[0] * predicted_output[0][0], self.target_sz[1] * predicted_output[0][1]]
                    self.pos = [self.pos[0] + self.target_sz[0] * predicted_output[0][0],
                                self.pos[1] + self.target_sz[1] * predicted_output[0][1]]

                    self.pos = [max(self.target_sz[0] / 2, min(self.pos[0], self.im_sz[0] - self.target_sz[0] / 2)),
                                max(self.target_sz[1] / 2, min(self.pos[1], self.im_sz[1] - self.target_sz[1] / 2))]
                ##################################################################################
                # we need to train the tracker again for scaling, it's almost the replicate of train
                ##################################################################################
                # calculate the new target size
                # scale_change = predicted_output[0][2:]
                # self.target_sz = np.multiply(self.target_sz, scale_change.mean())
                # we also require the target size to be smaller than the image size deivided by paddings

            # HDT
            self.maxres = np.asarray([np.max(x) for x in self.response])
            if not self.sub_sub_sub_feature_type == 'maximum_res':
                for ii in range(len(self.response)):
                    self.loss[-1, ii] = self.maxres[ii] - \
                                        self.response[ii][
                                            np.clip(np.rint(predicted_output[0][0] * self.response[ii].shape[0]), 0, self.response[ii].shape[0]-1),
                                            np.clip(np.rint(predicted_output[0][1] * self.response[ii].shape[1]), 0, self.response[ii].shape[1]-1)]

                # update the loss history
                self.LosIdx = np.mod(frame - 1, 5)
                self.loss[self.LosIdx] = self.loss[-1]

                if frame > self.loss_acc_time:
                    self.lossA = np.sum(np.multiply(self.W, self.loss[-1]))
                    self.LosMean = np.mean(self.loss[:self.loss_acc_time], axis=0)
                    self.LosStd = np.std(self.loss[:self.loss_acc_time], axis=0)
                    self.LosMean[self.LosMean < 0.0001] = 0
                    self.LosStd[self.LosStd < 0.0001] = 0
                    self.curDiff = self.loss[-1] - self.LosMean
                    self.stability = np.divide(np.abs(self.curDiff), self.LosStd + np.finfo(float).eps)
        elif self.feature_type == 'HDT':
            self.maxres = np.zeros(shape=len(self.response))
            self.expert_row = np.zeros(shape=len(self.response))
            self.expert_col = np.zeros(shape=len(self.response))
            self.response_all = np.zeros(shape=(len(self.response), self.resize_size[0], self.resize_size[1]))
            self.cell_size_all = np.zeros(shape=(len(self.response), 2))
            # we reshape the response to the same size
            for l in range(len(self.response)):
                rm = self.response[l]
                rm_resize = imresize(rm, self.resize_size)
                self.response_all[l] = rm_resize
                #rm_resize = rm_resize * 1.0 /rm_resize.max() * rm.max()
                #self.response[l] = rm_resize

            # The used gt at the 2nd frame can be replaced by gt in the 1st frame since
            # most of the targets in videos move slightly  between frames.
            # It can also be obtained  by another tracker user specified.
            row = 0
            col = 0
            for ii in range(len(self.response)):
                self.maxres[ii] = self.response[ii].max()
                self.expert_row[ii], self.expert_col[ii] = np.unravel_index(self.response[ii].argmax(), self.response[ii].shape)

                self.cell_size_all[ii] = np.divide(self.patch_size, self.response[ii].shape)
                row += self.W[ii] * self.expert_row[ii] * self.cell_size_all[ii][0]
                col += self.W[ii] * self.expert_col[ii] * self.cell_size_all[ii][1]

            self.vert_delta, self.horiz_delta = [row - self.patch_size[0] / 2.,
                                                 col - self.patch_size[1] / 2.]

            self.pos = [self.pos[0] + self.vert_delta, self.pos[1] + self.horiz_delta]

            for ii in range(len(self.response)):
                self.loss[-1, ii] = self.maxres[ii] - \
                                    self.response[ii][np.rint(row / self.cell_size_all[ii][0]), np.rint(col / self.cell_size_all[ii][1])]

            # update the loss history
            self.LosIdx = np.mod(frame - 1, 5)
            self.loss[self.LosIdx] = self.loss[-1]

            if frame > self.loss_acc_time:
                self.lossA = np.sum(np.multiply(self.W, self.loss[-1]))
                self.LosMean = np.mean(self.loss[:self.loss_acc_time], axis=0)
                self.LosStd = np.std(self.loss[:self.loss_acc_time], axis=0)
                self.LosMean[self.LosMean < 0.0001] = 0
                self.LosStd[self.LosStd < 0.0001] = 0

                self.curDiff = self.loss[-1] - self.LosMean
                self.stability = np.divide(np.abs(self.curDiff), self.LosStd+np.finfo(float).eps)
                self.alpha = 0.97 * np.exp((-1 * self.stability))

                # truncation
                self.alpha[self.alpha > 0.97] = 0.97
                self.alpha[self.alpha < 0.12] = 0.12
                self.R = np.multiply(self.R, self.alpha) + np.multiply((1-self.alpha), self.lossA - self.loss[-1])
                print("Regret is {0}".format(self.R))
                self.c = self.find_nh_scale(self.R, self.A)
                self.W = self.nnhedge_weights(self.R, self.c, self.A)
                self.W = np.clip(self.W / np.sum(self.W), 0.001, 1)
                self.W = self.W / np.sum(self.W)
            print("W is {0}".format(self.W))
        ##################################################################################
        # we need to train the tracker again here, it's almost the replicate of train
        ##################################################################################
        if self.sub_feature_type == 'dsst':
            xs = self.get_scale_sample(im, self.currentScaleFactor * self.scaleFactors)
            xsf = np.fft.fftn(xs, axes=[0])
            # calculate the correlation response of the scale filter
            scale_response_fft = np.divide(np.multiply(self.sf_num, xsf),
                                           (self.sf_den[:, None] + self.lambda_scale))
            scale_reponse = np.real(np.fft.ifftn(np.sum(scale_response_fft, axis=1)))
            recovered_scale = np.argmax(scale_reponse)
            # update the scale
            self.currentScaleFactor *= self.scaleFactors[recovered_scale]
            if self.currentScaleFactor < self.min_scale_factor:
                self.currentScaleFactor = self.min_scale_factor
            elif self.currentScaleFactor > self.max_scale_factor:
                self.currentScaleFactor = self.max_scale_factor
            # we only update the target size here.
            new_target_sz = np.multiply(self.currentScaleFactor, self.first_target_sz)
            self.pos -= (new_target_sz-self.target_sz)/2
            self.target_sz = new_target_sz
            self.patch_size = np.multiply(self.target_sz, (1 + self.padding))
        elif self.sub_feature_type == 'dnn_scale':
            scale_reponse = []
            xs = self.get_scale_sample_dnn(im, self.currentScaleFactor * self.scaleFactors)
            xsf = np.fft.fftn(xs, axes=(1, 2))
            for z, zf in zip(xs, xsf):
                k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, self.xf[0], self.x[0], zf, z)
                kf = self.fft2(k)
                scale_reponse.append(np.real(np.fft.ifft2(np.multiply(self.alphaf[0], kf))))
            recovered_scale = np.argmax(np.multiply(np.asarray([np.max(x) for x in scale_reponse]), self.scale_window))
            # update the scale
            self.currentScaleFactor *= self.scaleFactors[recovered_scale]
            if self.currentScaleFactor < self.min_scale_factor:
                self.currentScaleFactor = self.min_scale_factor
            elif self.currentScaleFactor > self.max_scale_factor:
                self.currentScaleFactor = self.max_scale_factor
            # we only update the target size here.
            new_target_sz = np.multiply(self.currentScaleFactor, self.first_target_sz)
            self.pos -= (new_target_sz - self.target_sz) / 2
            self.target_sz = new_target_sz
            self.patch_size = np.multiply(self.target_sz, (1 + self.padding))

        # we update the model from here
        self.im_crop = self.get_subwindow(im, self.pos, self.patch_size)
        x_new = self.get_features()
        if self.feature_type == 'multi_cnn' or self.feature_type == 'HDT':
            if self.saliency == 'grabcut' and self.use_saliency:
                for l in range(len(x_new)):
                    x_new[l] = x_new[l][:, :, self.feature_valid_idx[l]]
                    if self.cross_correlation > 0:
                        chosen_number = np.sum(self.feature_correlation[l]>self.cross_correlation)
                        x_new[l] = x_new[l][:, :, self.feature_correlation_ranked_idx[l][-chosen_number:][::-1]]
                    elif self.saliency_percent < 1.0:
                        chosen_number = int(self.saliency_percent * len(self.feature_correlation_ranked_idx[l]))
                        x_new[l] = x_new[l][:, :, self.feature_correlation_ranked_idx[l][-chosen_number:][::-1]]
                    else:
                        x_new[l] *= self.feature_correlation[l][None, None, :]
            xf_new = self.fft2(x_new)
            # we follow the paper for Hedged Deep tracking for updating the learning rate
            self.max_list = [np.max(x) for x in self.response]
            self.response_max_list = []
            if not (self.sub_sub_sub_feature_type == 'maximum_res' or self.feature_type == 'HDT'):
                for rm in self.response:
                    row, col = rm.shape
                    row_max = max(0, min((1./2 + predicted_output[0][0]) * row, row-1))
                    col_max = max(0, min((1./2 + predicted_output[0][1]) * col, col-1))
                    self.response_max_list.append(rm[int(row_max), int(col_max)])
            if self.sub_sub_feature_type == 'adapted_lr_hdt':
                loss_idx = np.mod(frame, self.acc_time)
                self.loss[loss_idx] = np.asarray(self.max_list) - np.asarray(self.response_max_list)
                self.loss_mean = np.mean(self.loss, axis=0)
                self.loss_std = np.std(self.loss, axis=0)

                if frame > self.acc_time:
                    self.stability = np.abs(self.loss[loss_idx]-self.loss_mean) / (self.loss_std + np.finfo(np.float32).eps)
                    # stability value is small(0), object is stable, adaptive learning rate is increased to maximum
                    # stability value is big(1), object is not stable, adaptive learning rate is decreased to minimum
                    self.adaptation_rate = self.stability*(self.adaptation_rate_range[0] - self.adaptation_rate_range[1])
                    self.adaptation_rate_scale = self.stability.mean()*(self.adaptation_rate_scale_range[0] - self.adaptation_rate_scale_range[1])

                for i in range(len(x_new)):
                    k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, xf_new[i], x_new[i])
                    if self.reg_method:
                        reg_map = self.fft2(1 - self.y[i] + self.reg_min)
                        reg_map = np.fft.fftshift(np.fft.fftshift(reg_map, axes=0), axes=1)
                        reg = np.multiply(reg_map, np.conj(reg_map))
                        alphaf_new = np.divide(self.yf[i], self.fft2(k) + reg * self.reg_mul)
                        self.alphaf.append(alphaf_new)
                        if False:
                            plt.figure(1)
                            alpha_real = np.real(np.multiply(alphaf_new, np.conj(alphaf_new)))
                            alpha_real = np.fft.fftshift(np.fft.fftshift(alpha_real, axes=0), axes=1)
                            print(alpha_real.max())
                            plt.imshow(alpha_real / alpha_real.max())

                            a2 = np.divide(self.yf[i], self.fft2(k) + self.lambda_value)
                            a2 = np.fft.fftshift(np.fft.fftshift(a2, axes=0), axes=1)
                            plt.figure(2)
                            a2_real = np.real(np.multiply(a2, np.conj(a2)))
                            print(a2_real.max())
                            plt.imshow(a2_real / a2_real.max())
                    else:
                        alphaf_new = np.divide(self.yf[i], self.fft2(k) + self.lambda_value)
                    self.x[i] = (1 - self.adaptation_rate[i]) * self.x[i] + self.adaptation_rate[i] * x_new[i]
                    self.xf[i] = (1 - self.adaptation_rate[i]) * self.xf[i] + self.adaptation_rate[i] * xf_new[i]
                    self.alphaf[i] = (1 - self.adaptation_rate[i]) * self.alphaf[i] + self.adaptation_rate[i] * alphaf_new
            else:
                adaptation_rate = self.stability * self.adaptation_rate
                # if frame > len(self.stability):
                #     print("stability is {0}".format(self.stability))
                for i in range(len(x_new)):
                    if self.kernel == 'gaussian':
                        k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, xf_new[i], x_new[i])
                        kf = self.fft2(k)
                    elif self.kernel == 'linear':
                        kf = self.linear_kernel(xf_new[i])

                    alphaf_new = np.divide(self.yf[i], kf + self.lambda_value)
                    self.x[i] = (1 - adaptation_rate[i]) * self.x[i] + adaptation_rate[i] * x_new[i]
                    self.xf[i] = (1 - adaptation_rate[i]) * self.xf[i] + adaptation_rate[i] * xf_new[i]
                    self.alphaf[i] = (1 - adaptation_rate[i]) * self.alphaf[i] + adaptation_rate[i] * alphaf_new
                    # self.x[i] = (1 - self.adaptation_rate) * self.x[i] + self.adaptation_rate * x_new[i]
                    # self.xf[i] = (1 - self.adaptation_rate) * self.xf[i] + self.adaptation_rate * xf_new[i]
                    # self.alphaf[i] = (1 - self.adaptation_rate) * self.alphaf[i] + self.adaptation_rate * alphaf_new
            if self.sub_feature_type == 'dsst':
                xs = self.get_scale_sample(im, self.currentScaleFactor * self.scaleFactors)
                xsf = np.fft.fftn(xs, axes=[0])
                # we use linear kernel as in the BMVC2014 paper
                new_sf_num = np.multiply(self.ysf[:, None], np.conj(xsf))
                new_sf_den = np.real(np.sum(np.multiply(xsf, np.conj(xsf)), axis=1))
                self.sf_num = (1 - self.adaptation_rate_scale) * self.sf_num + self.adaptation_rate_scale * new_sf_num
                self.sf_den = (1 - self.adaptation_rate_scale) * self.sf_den + self.adaptation_rate_scale * new_sf_den
        else:
            xf_new = self.fft2(x_new)
            if self.kernel == 'gaussian':
                k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, self.xf[i], self.x[i], zf[i], z[i])
                kf = self.fft2(k)
            elif self.kernel == 'linear':
                kf = self.linear_kernel(self.xf, zf)

            alphaf_new = np.divide(self.yf, kf + self.lambda_value)
            self.x = (1 - self.adaptation_rate) * self.x + self.adaptation_rate * x_new
            self.xf = (1 - self.adaptation_rate) * self.xf + self.adaptation_rate * xf_new
            self.alphaf = (1 - self.adaptation_rate) * self.alphaf + self.adaptation_rate * alphaf_new

        # we also require the bounding box to be within the image boundary
        self.res.append([min(self.im_sz[1] - self.target_sz[1], max(0, self.pos[1] - self.target_sz[1] / 2.)),
                         min(self.im_sz[0] - self.target_sz[0], max(0, self.pos[0] - self.target_sz[0] / 2.)),
                         self.target_sz[1], self.target_sz[0]])

        return self.pos

    def find_nh_scale(self, regrets, A):

        def avgnh(r, c, A):
            n = np.prod(r.shape)
            T = r + A
            T[T<0] = 0
            w = np.exp(0.5 * np.multiply(T, T) / c)
            total = (1./n) * np.sum(w) - 2.72
            return total

        # first find an upper and lower bound on c, based on the nh weights
        clower = 1
        counter = 0
        while avgnh(regrets, clower, A) < 0 and counter < 30:
            clower *= 0.5
            counter += 1

        cupper = 1
        counter = 0
        while avgnh(regrets, cupper, A) > 0 and counter < 30:
            cupper *= 2
            counter += 1

        # now dow a binary search
        cmid = (cupper + clower) /2
        counter = 0
        while np.abs(avgnh(regrets, cmid, A))> 1e-2 and counter < 30:
            if avgnh(regrets, cmid, A) > 1e-2:
                clower = cmid
                cmid = (cmid + cupper) / 2
            else:
                cupper = cmid
                cmid = (cmid + clower) / 2
            counter += 1

        return cmid

    def nnhedge_weights(self, r, scale, A):
        n = np.prod(r.shape)
        w = np.zeros(shape=n)

        for i in range(n):
            if r[i] + A <= 0:
                w[i] = 0
            else:
                w[i] = (r[i] + A)/scale * np.exp((r[i] + A) * (r[i] + A) / (2 * scale))
        return w

    def dense_gauss_kernel(self, sigma, xf, x, zf=None, z=None):
        """
        Gaussian Kernel with dense sampling.
        Evaluates a gaussian kernel with bandwidth SIGMA for all displacements
        between input images X and Y, which must both be MxN. They must also
        be periodic (ie., pre-processed with a cosine window). The result is
        an MxN map of responses.

        If X and Y are the same, ommit the third parameter to re-use some
        values, which is faster.
        :param sigma: feature bandwidth sigma
        :param x:
        :param y: if y is None, then we calculate the auto-correlation
        :return:
        """
        N = xf.shape[0]*xf.shape[1]
        xx = np.dot(x.flatten().transpose(), x.flatten())  # squared norm of x

        if zf is None:
            # auto-correlation of x
            zf = xf
            zz = xx
        else:
            zz = np.dot(z.flatten().transpose(), z.flatten())  # squared norm of y

        xyf = np.multiply(zf, np.conj(xf))
        if self.feature_type == 'raw' or self.feature_type == 'dsst':
            if len(xyf.shape) == 3:
                xyf_ifft = np.fft.ifft2(np.sum(xyf, axis=2))
            elif len(xyf.shape) == 2:
                xyf_ifft = np.fft.ifft2(xyf)
            # elif len(xyf.shape) == 4:
            #     xyf_ifft = np.fft.ifft2(np.sum(xyf, axis=3))
        elif self.feature_type == 'hog':
            xyf_ifft = np.fft.ifft2(np.sum(xyf, axis=2))
        elif self.feature_type == 'vgg' or self.feature_type == 'resnet50' \
                or self.feature_type == 'vgg_rnn' or self.feature_type == 'cnn' \
                or self.feature_type =='multi_cnn' or self.feature_type =='HDT':
            xyf_ifft = np.fft.ifft2(np.sum(xyf, axis=2))

        row_shift, col_shift = np.floor(np.array(xyf_ifft.shape) / 2).astype(int)
        xy_complex = np.roll(xyf_ifft, row_shift, axis=0)
        xy_complex = np.roll(xy_complex, col_shift, axis=1)
        c = np.real(xy_complex)
        d = np.real(xx) + np.real(zz) - 2 * c
        k = np.exp(-1. / sigma**2 * np.maximum(0, d) / N)

        return k

    def get_subwindow(self, im, pos, sz):
        """
        Obtain sub-window from image, with replication-padding.
        Returns sub-window of image IM centered at POS ([y, x] coordinates),
        with size SZ ([height, width]). If any pixels are outside of the image,
        they will replicate the values at the borders.

        The subwindow is also normalized to range -0.5 .. 0.5, and the given
        cosine window COS_WINDOW is applied
        (though this part could be omitted to make the function more general).
        """

        if np.isscalar(sz):  # square sub-window
            sz = [sz, sz]

        ys = np.floor(pos[0]) + np.arange(sz[0], dtype=int) - np.floor(sz[0] / 2)
        xs = np.floor(pos[1]) + np.arange(sz[1], dtype=int) - np.floor(sz[1] / 2)

        ys = ys.astype(int)
        xs = xs.astype(int)

        # check for out-of-bounds coordinates and set them to the values at the borders
        ys[ys < 0] = 0
        ys[ys >= self.im_sz[0]] = self.im_sz[0] - 1

        xs[xs < 0] = 0
        xs[xs >= self.im_sz[1]] = self.im_sz[1] - 1

        # extract image

        if self.feature_type == 'raw' or self.feature_type == 'dsst':
            out = im[np.ix_(ys, xs)]
            # introduce scaling, here, we need them to be the same size
            if np.all(self.first_patch_sz == out.shape[:2]):
                return out
            else:
                out = imresize(out, self.first_patch_sz)
                return out / 255.
        elif self.feature_type == 'vgg' or self.feature_type == 'resnet50' or \
             self.feature_type == 'vgg_rnn' or self.feature_type == 'cnn' \
                or self.feature_type == 'multi_cnn' or self.feature_type == 'HDT':
            c = np.array(range(3))
            out = im[np.ix_(ys, xs, c)]
            # if self.feature_type == 'vgg_rnn' or self.feature_type == 'cnn':
            #     from keras.applications.vgg19 import preprocess_input
            #     x = imresize(out.copy(), self.resize_size)
            #     out = np.multiply(x, self.cos_window_patch[:, :, None])
            return out

    def exclude_subwindow_coorindate(self, flow, pos, sz):
        """
        Obtain sub-window from image, with replication-padding.
        Returns sub-window of image IM centered at POS ([y, x] coordinates),
        with size SZ ([height, width]). If any pixels are outside of the image,
        they will replicate the values at the borders.

        The subwindow is also normalized to range -0.5 .. 0.5, and the given
        cosine window COS_WINDOW is applied
        (though this part could be omitted to make the function more general).
        """

        if np.isscalar(sz):  # square sub-window
            sz = [sz, sz]

        ys = np.floor(pos[0]) + np.arange(sz[0], dtype=int) - np.floor(sz[0] / 2)
        xs = np.floor(pos[1]) + np.arange(sz[1], dtype=int) - np.floor(sz[1] / 2)

        ys = ys.astype(int)
        xs = xs.astype(int)

        # check for out-of-bounds coordinates and set them to the values at the borders
        ys[ys < 0] = 0
        ys[ys >= self.im_sz[0]] = self.im_sz[0] - 1

        xs[xs < 0] = 0
        xs[xs >= self.im_sz[1]] = self.im_sz[1] - 1

        # exclude image patch image
        c = np.array(range(2))
        flow[np.ix_(ys, xs, c)] = 0
        return flow

    def fft2(self, x):
        """
        FFT transform of the first 2 dimension
        :param x: M*N*C the first two dimensions are used for Fast Fourier Transform
        :return:  M*N*C the FFT2 of the first two dimension
        """
        if type(x) == list:
            x = [np.fft.fft2(f, axes=(0,1)) for f in x]
            return x
        else:
            return np.fft.fft2(x, axes=(0, 1))

    def get_features(self):
        """
        :param im: input image
        :return:
        """
        if self.feature_type == 'raw':
            #using only grayscale:
            if len(self.im_crop.shape) == 3:
                if self.sub_feature_type == 'gray':
                    img_gray = np.mean(self.im_crop, axis=2)
                    img_gray = img_gray - img_gray.mean()
                    features = np.multiply(img_gray, self.cos_window)
                else:
                    img_colour = self.im_crop - self.im_crop.mean()
                    features = np.multiply(img_colour, self.cos_window[:, :, None])

        elif self.feature_type == 'dsst':
            img_colour = self.im_crop - self.im_crop.mean()
            features = np.multiply(img_colour, self.cos_window[:, :, None])

        elif self.feature_type == 'vgg' or self.feature_type == 'resnet50':
            if self.feature_type == 'vgg':
                from keras.applications.vgg19 import preprocess_input
            elif self.feature_type == 'resnet50':
                from keras.applications.resnet50 import preprocess_input
            x = np.expand_dims(self.im_crop.copy(), axis=0)
            x = preprocess_input(x)
            features = self.extract_model.predict(x)
            features = np.squeeze(features)
            features = (features.transpose(1, 2, 0) - features.min()) / (features.max() - features.min())
            features = np.multiply(features, self.cos_window[:, :, None])

        elif self.feature_type == 'vgg_rnn' or self.feature_type=='cnn':
            from keras.applications.vgg19 import preprocess_input
            x = imresize(self.im_crop.copy(), self.resize_size)
            x = x.transpose((2, 0, 1)).astype(np.float64)
            x = np.expand_dims(x, axis=0)
            x = preprocess_input(x)
            features = self.extract_model.predict(x)
            features = np.squeeze(features)
            features = (features.transpose(1, 2, 0) - features.min()) / (features.max() - features.min())
            features = np.multiply(features, self.cos_window[:, :, None])

        elif self.feature_type == "multi_cnn":
            from keras.applications.vgg19 import preprocess_input
            x = imresize(self.im_crop.copy(), self.resize_size)
            #x = x.transpose((2, 0, 1)).astype(np.float64)
            x = np.expand_dims(x, axis=0).astype(np.float32)
            x = preprocess_input(x)
            if keras.backend._backend == 'theano':
                features_list = self.extract_model_function(x)
            else:
                features_list = self.extract_model_function([x])
            for i, features in enumerate(features_list):
                features = np.squeeze(features)
                features = (features - features.min()) / (features.max() - features.min())
                features_list[i] = np.multiply(features, self.cos_window[i][:, :, None])
            return features_list
        elif self.feature_type == "HDT":
            from keras.applications.vgg19 import preprocess_input
            x = imresize(self.im_crop.copy(), self.resize_size)
            x = x.transpose((2, 0, 1)).astype(np.float64)
            x = np.expand_dims(x, axis=0)
            x = preprocess_input(x)
            features_list = self.extract_model_function(x)
            for i, features in enumerate(features_list):
                features = np.squeeze(features)
                features = (features.transpose(1, 2, 0) - features.min()) / (features.max() - features.min())
                features_list[i] = np.multiply(features, self.cos_window[i][:, :, None])
                #features_list[i] = np.multiply(features.transpose(1, 2, 0), self.cos_window[i][:, :, None])
            return features_list
        else:
            assert 'Non implemented!'

        if not (self.sub_feature_type=="" or self.feature_correlation is None):
            features = np.multiply(features, self.feature_correlation[None, None, :])
        return features

    def get_scale_sample(self, im, scaleFactors):
        from pyhog import pyhog
        resized_im_array = []
        for i, s in enumerate(scaleFactors):
            patch_sz = np.floor(self.first_target_sz * s)
            im_patch = self.get_subwindow(im, self.pos, patch_sz)  # extract image
            # because the hog output is (dim/4)-2>1:
            if self.first_target_sz.min()<12:
                scale_up_factor = 12. / np.min(self.first_target_sz)
                im_patch_resized = imresize(im_patch, np.asarray(self.first_target_sz * scale_up_factor).astype('int'))
            else:
                im_patch_resized = imresize(im_patch, self.first_target_sz)  #resize image to model size
            features_hog = pyhog.features_pedro(im_patch_resized.astype(np.float64)/255.0, 4)
            resized_im_array.append(np.multiply(features_hog.flatten(), self.scale_window[i]))
            #resized_im_array.append(features_hog.flatten())
        return np.asarray(resized_im_array)

    def get_scale_sample_dnn(self, im, scaleFactors):
        from keras.applications.vgg19 import preprocess_input
        resized_im_array = np.zeros(shape=(len(scaleFactors), self.resize_size[0], self.resize_size[1], 3))
        for i, s in enumerate(scaleFactors):
            # patch_sz = np.floor(self.first_target_sz * s)
            patch_sz = np.rint(self.first_patch_sz * s)
            im_patch = self.get_subwindow(im, self.pos, patch_sz)  # extract image
            # resize image to model size
            resized_im_array[i] = imresize(im_patch, self.resize_size)

        dnn_input = resized_im_array.transpose(0, 3, 1, 2).astype(np.float64)
        dnn_input = preprocess_input(dnn_input)
        features_list = self.extract_model_function(dnn_input)
        features = features_list[0]
        features = (features.transpose(0, 2, 3, 1) - features.min()) / (features.max() - features.min())
        features = np.multiply(features, self.cos_window[0][None, :, :, None])
        return features

    def train_rnn(self, frame, im, init_rect, target_sz, img_rgb_next, next_rect, next_target_sz):

        self.pos = [init_rect[1] + init_rect[3] / 2., init_rect[0] + init_rect[2] / 2.]
        # Duh OBT is the reverse
        self.target_sz = target_sz[::-1]
        # desired padded input, proportional to input target size
        self.patch_size = np.floor(self.target_sz * (1 + self.padding))
        self.im_sz = im.shape[1:]

        if frame==0:
            self.im_crop = self.get_subwindow(im, self.pos, self.patch_size)
            self.x = self.get_features()
            self.xf = self.fft2(self.x)
            k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, self.xf, self.x)
            self.alphaf = np.divide(self.yf, self.fft2(k) + self.lambda_value)

        ###################### Next frame #####################################
        self.im_crop = self.get_subwindow(img_rgb_next, self.pos, self.patch_size)
        z = self.get_features()
        zf = self.fft2(z)
        k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, self.xf, self.x, zf, z)
        kf = self.fft2(k)
        self.response = np.real(np.fft.ifft2(np.multiply(self.alphaf, kf)))
        ##################################################################################
        # we need to train the tracker again here, it's almost the replicate of train
        ##################################################################################
        self.pos_next = [next_rect[1] + next_rect[3] / 2., next_rect[0] + next_rect[2] / 2.]
        self.im_crop = self.get_subwindow(img_rgb_next, self.pos_next, self.patch_size)
        x_new = self.get_features()
        xf_new = self.fft2(x_new)
        k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, xf_new, x_new)
        kf = self.fft2(k)
        alphaf_new = np.divide(self.yf, kf + self.lambda_value)
        self.x = (1 - self.adaptation_rate) * self.x + self.adaptation_rate * x_new
        self.xf = (1 - self.adaptation_rate) * self.xf + self.adaptation_rate * xf_new
        self.alphaf = (1 - self.adaptation_rate) * self.alphaf + self.adaptation_rate * alphaf_new


        lstm_input = self.response.flatten()
        lstm_input.resize(1, np.prod(self.response_size))
        pos_move = np.array([(self.pos_next[0] - self.pos[0]), (self.pos_next[1] - self.pos[1])])
        pos_move.resize(1, 2)
        self.lstm_model.fit(lstm_input, pos_move, batch_size=1, verbose=1, nb_epoch=1, shuffle=False)
        print('Predicting')
        predicted_output = self.lstm_model.predict(lstm_input, batch_size=1)
        print(pos_move)
        print(predicted_output)

    def train_cnn(self, frame, im, init_rect, img_rgb_next, next_rect, x_train, y_train, count):

        self.pos = [init_rect[1] + init_rect[3] / 2., init_rect[0] + init_rect[2] / 2.]
        # OTB is the reverse
        self.target_sz = np.asarray(init_rect[2:])
        self.target_sz = self.target_sz[::-1]
        self.next_target_sz = np.asarray(next_rect[2:])
        self.next_target_sz = self.next_target_sz[::-1]
        self.scale_change = np.divide(np.array(self.next_target_sz).astype(float), self.target_sz)
        # desired padded input, proportional to input target size
        self.patch_size = np.floor(self.target_sz * (1 + self.padding))
        self.im_sz = im.shape[:2]

        if frame == 0:
            self.im_crop = self.get_subwindow(im, self.pos, self.patch_size)
            self.x = self.get_features()
            self.xf = self.fft2(self.x)
            self.alphaf = []
            for i in range(len(self.x)):
                k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, self.xf[i], self.x[i])
                self.alphaf.append(np.divide(self.yf[i], self.fft2(k) + self.lambda_value))

        ###################### Next frame #####################################
        #t0 = time.clock()
        self.im_crop = self.get_subwindow(img_rgb_next, self.pos, self.patch_size)
        z = self.get_features()
        zf = self.fft2(z)
        #print(time.clock() - t0, "Feature process time")
        self.response = []
        for i in range(len(z)):
            k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, self.xf[i], self.x[i], zf[i], z[i])
            kf = self.fft2(k)
            self.response.append(np.real(np.fft.ifft2(np.multiply(self.alphaf[i], kf))))

        ##################################################################################
        # we need to train the tracker again here, it's almost the replicate of train
        ##################################################################################
        self.pos_next = [next_rect[1] + next_rect[3] / 2., next_rect[0] + next_rect[2] / 2.]
        self.im_crop = self.get_subwindow(img_rgb_next, self.pos_next, self.patch_size)
        x_new = self.get_features()
        xf_new = self.fft2(x_new)
        for i in range(len(x_new)):
            k = self.dense_gauss_kernel(self.feature_bandwidth_sigma, xf_new[i], x_new[i])
            kf = self.fft2(k)
            alphaf_new = np.divide(self.yf[i], kf + self.lambda_value)
            self.x[i] = (1 - self.adaptation_rate) * self.x[i] + self.adaptation_rate * x_new[i]
            self.xf[i] = (1 - self.adaptation_rate) * self.xf[i] + self.adaptation_rate * xf_new[i]
            self.alphaf[i] = (1 - self.adaptation_rate) * self.alphaf[i] + self.adaptation_rate * alphaf_new

        # we fill the matrix with zeros first
        response_all = np.zeros(shape=(5, self.resize_size[0], self.resize_size[1]))

        for i in range(len(self.response)):
            response_all[i, :self.response[i].shape[0], :self.response[i].shape[1]] = self.response[i]

        x_train[count, :, :, :] = response_all
        self.pos_next = [next_rect[1] + next_rect[3] / 2., next_rect[0] + next_rect[2] / 2.]
        pos_move = np.array([(self.pos_next[0] - self.pos[0]) * 1.0 / self.target_sz[0],
                             (self.pos_next[1] - self.pos[1]) * 1.0 / self.target_sz[1]])
        y_train[count, :] = np.concatenate([pos_move, self.scale_change])
        count += 1
        return x_train, y_train, count

        # ('feature time:', 0.07054710388183594)
        # ('fft2:', 0.22904396057128906)
        # ('guassian kernel + fft2: ', 0.20537400245666504)

    def draw_flow(self, img, flow, step=16):
        h, w = img.shape[:2]
        y, x = np.mgrid[step/2:h:step, step/2:w:step].reshape(2,-1).astype(int)
        fx, fy = flow[y,x].T
        lines = np.vstack([x, y, x+fx, y+fy]).T.reshape(-1, 2, 2)
        lines = np.int32(lines + 0.5)
        vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        cv2.polylines(vis, lines, 0, (0, 255, 0))
        for (x1, y1), (x2, y2) in lines:
            cv2.circle(vis, (x1, y1), 1, (0, 255, 0), -1)
        return vis

    def linear_kernel(self, xf, zf=None):
        if zf is None:
            zf = xf
        N = np.prod(xf.shape)
        xyf = np.multiply(zf, np.conj(xf))
        kf = np.sum(xyf, axis=2)
        return kf / N