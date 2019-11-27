from __future__ import print_function
import hashlib
from multiprocessing import Pool, Process, cpu_count
# from multiprocessing.dummy import Pool, Process, cpu_count

from array import array
import numpy as np
import time
from modules.PlotConfigs import HistogramCfg
from modules.DataMCPlot import DataMCPlot
# from modules.DDE import DDE
from modules.binning import binning_dimuonmass
from modules.path_to_NeuralNet import path_to_NeuralNet
# from modules.nn import run_nn 
# import modules.fr_net as fr_net
# from CMGTools.RootTools.DataMC.Histogram import Histogram
from modules.SignalReweighter import reweightSignals
from pdb import set_trace

from ROOT import ROOT, RDataFrame, TH1F, TFile, TTree, TTreeFormula, gInterpreter, gROOT, gSystem


# Enable ROOT's implicit multi-threading for all objects that provide an internal parallelisation mechanism
# ROOT.EnableImplicitMT(40)
ROOT.EnableImplicitMT()

def initHist(hist, vcfg):
    hist.Sumw2()
    xtitle = vcfg.xtitle
    if vcfg.unit:
        xtitle += ' ({})'.format(vcfg.unit)
    hist.GetXaxis().SetTitle(xtitle)
    hist.SetStats(False)

class CreateHists(object):
    def __init__(self, hist_cfg, analysis_dir = '/home/dehuazhu/SESSD/4_production/', channel = 'mmm', server = 'starseeker', useNeuralNetwork=False, dataset='2017',hostname='starseeker'):
        self.analysis_dir = analysis_dir
        self.channel = channel
        self.dataset = dataset
        self.server = server
        self.hist_cfg = hist_cfg
        self.useNeuralNetwork = useNeuralNetwork
        self.hostname = hostname
        if self.hist_cfg.vars:
            self.vcfgs = hist_cfg.vars

        if not self.vcfgs:
            print ('ERROR in createHistograms: No variable configs passed', self.hist_cfg.name)

        self.plots = {}

        for vcfg in self.vcfgs:
            plot = DataMCPlot(vcfg.name,vcfg.xtitle)
            plot.lumi = hist_cfg.lumi
            if vcfg.name in self.plots:
                print ('Adding variable with same name twice', vcfg.name, 'not yet foreseen; taking the last')
            self.plots[vcfg.name] = plot

    def createHistograms(self, hist_cfg, all_stack=False, verbose=False,  vcfgs=None, multiprocess = True, useNeuralNetwork = False, signalReweight = False):
        if multiprocess == True:
            #using multiprocess to create the histograms
            nSamples = len(self.hist_cfg.cfgs)
            pool = Pool(processes= nSamples)
            results = pool.map(self.makealltheplots, self.hist_cfg.cfgs) 
            pool.terminate()

            for vcfg in self.vcfgs:
                for result in results: 
                    self.plots[vcfg.name].AddHistogram(\
                            result[vcfg.name].histos[0].name\
                            ,result[vcfg.name].histos[0].obj\
                            ,stack=result[vcfg.name].histos[0].stack)
            
            if signalReweight:
                starttime_reweight = time.time()
                reweightCfgs = reweightSignals(self.plots, useMultiprocess = True, ana_dir = self.analysis_dir, hist_cfg = self.hist_cfg, channel = self.channel)

                # #adding reweighting for multiprocessing; be careful when using this option to make sure you have enough memory!
                # # The number of samples can go up to 100!
                # # reweightPool = Pool(processes=len(reweightCfgs))
                # reweightPool = Pool(10)
                # reWeightResults = reweightPool.map(self.makealltheplots, reweightCfgs) 
                # reweightPool.terminate()
                # for vcfg in self.vcfgs:
                    # for reWeightResult in reWeightResults: 
                        # self.plots[vcfg.name].AddHistogram(\
                                # reWeightResult[vcfg.name].histos[nSamples].name\
                                # ,reWeightResult[vcfg.name].histos[nSamples].obj\
                                # ,stack=reWeightResult[vcfg.name].histos[nSamples].stack)

                #adding reweighting for non multiprocessing
                for cfg in reweightCfgs:
                    result = self.makealltheplots(cfg)
                    print('done sample nr. %d; passed time: %d seconds'%(len(self.plots[vcfg.name].histos),time.time()-starttime_reweight),end='\r')

       
        if multiprocess == False:
            for i, cfg in enumerate(self.hist_cfg.cfgs):
                # result = self.makealltheplots(self.hist_cfg.cfgs[i]) 
                try:
                    result = self.makealltheplots(self.hist_cfg.cfgs[i]) 
                except:
                    set_trace()
       
            #adding reweighting for non multiprocessing
            reweightCfgs = reweightSignals(self.plots, useMultiprocess = False, ana_dir = self.analysis_dir, hist_cfg = self.hist_cfg, channel = self.channel)
            for cfg in reweightCfgs:
                result = self.makealltheplots(cfg)


        procs = []
        for plot_key in self.plots:
            proc = Process(target=self.plots[plot_key].Draw, args=())
            procs.append(proc)
            proc.start()
     
        for proc in procs:
            proc.join()       

        # for plot in self.plots.itervalues():
            # plot.Draw()


        return self.plots

    def makealltheplots(self, cfg):
        verbose=False
        all_stack=False
        if isinstance(cfg, HistogramCfg):
            hists = createHistograms(cfg, all_stack=True, vcfgs=self.vcfgs)
            for h in hists: print(h)
            for vcfg in self.vcfgs:
                hist = hists[vcfg.name]
                plot = self.plots[vcfg.name]
                hist._BuildStack(hist._SortedHistograms(), ytitle='Events')
                print('stack built')
                total_hist = plot.AddHistogram(cfg.name, hist.stack.totalHist.weighted, stack=True)

                if cfg.norm_cfg is not None:
                    norm_hist = createHistogram(cfg.norm_cfg, all_stack=True)
                    norm_hist._BuildStack(norm_hist._SortedHistograms(), ytitle='Events')
                    total_hist.Scale(hist.stack.integral/total_hist.Yield())

                if cfg.total_scale is not None:
                    total_hist.Scale(cfg.total_scale)
                    # print 'Scaling total', hist_cfg.name, 'by', cfg.total_scale
        else:
            # print('building histgrams for %s'%cfg.name)

            # Now read the tree
            if cfg.is_signal:
                tree_file_name = '/'.join([cfg.ana_dir, cfg.dir_name, cfg.tree_prod_name, 'tree_with_p_instead_of_dot.root'])
            else:
                tree_file_name = '/'.join([cfg.ana_dir, cfg.dir_name, cfg.tree_prod_name, 'tree.root'])

            # attach the trees to the first DataMCPlot
            plot = self.plots[self.vcfgs[0].name]
            try:
                if self.useNeuralNetwork:
                    if cfg.is_singlefake:
                        friend_file_name = path_to_NeuralNet('nonprompt',self.channel,self.dataset,self.hostname) + 'friendtree_fr_%s.root'%cfg.name
                        dataframe = plot.makeRootDataFrameFromTree(tree_file_name, cfg.tree_name, verbose=verbose, friend_name='SF2', friend_file_name=friend_file_name)
                        dataframe = plot.makeRootDataFrameFromTree(tree_file_name, cfg.tree_name, verbose=verbose, friend_name='SF1', friend_file_name=friend_file_name)
                    if cfg.is_doublefake:
                        friend_file_name = path_to_NeuralNet('nonprompt',self.channel,self.dataset,self.hostname) + 'friendtree_fr_%s.root'%cfg.name
                        dataframe = plot.makeRootDataFrameFromTree(tree_file_name, cfg.tree_name, verbose=verbose, friend_name='DF', friend_file_name=friend_file_name)
                    if cfg.is_nonprompt:
                        friend_file_name = path_to_NeuralNet('nonprompt',self.channel,self.dataset,self.hostname) + 'friendtree_fr_%s.root'%cfg.name
                        dataframe = plot.makeRootDataFrameFromTree(tree_file_name, cfg.tree_name, verbose=verbose, friend_name='nonprompt', friend_file_name=friend_file_name)
                    if cfg.is_contamination:
                        friend_file_name = path_to_NeuralNet('nonprompt',self.channel,self.dataset,self.hostname) + 'friendtree_fr_%s.root'%cfg.name
                        dataframe = plot.makeRootDataFrameFromTree(tree_file_name, cfg.tree_name, verbose=verbose, friend_name='nonprompt', friend_file_name=friend_file_name)
                    else:
                        dataframe = plot.makeRootDataFrameFromTree(tree_file_name, cfg.tree_name, verbose=verbose)
                else:
                    dataframe = plot.makeRootDataFrameFromTree(tree_file_name, cfg.tree_name, verbose=verbose)

            except:
                #This is for debugging
                print ('problem with %s; dataset = %s'%(cfg.name,self.dataset))
                set_trace()


            if cfg.is_singlefake == True:
                norm_cut  = self.hist_cfg.region.SF_LL
                self.norm_cut_LL  = self.hist_cfg.region.SF_LL
                self.norm_cut_LT  = self.hist_cfg.region.SF_LT
                self.norm_cut_TL  = self.hist_cfg.region.SF_TL

            if cfg.is_doublefake == True:
                norm_cut  = self.hist_cfg.region.DF
            
            if cfg.is_nonprompt == True:
                norm_cut  = self.hist_cfg.region.nonprompt

            if cfg.is_MC == True:
                # norm_cut  = self.hist_cfg.region.MC
                norm_cut  = self.hist_cfg.region.MC_contamination_pass

            if cfg.is_SingleConversions == True:
                norm_cut  = self.hist_cfg.region.MC_contamination_pass

            if cfg.is_DoubleConversions == True:
                norm_cut  = self.hist_cfg.region.MC_contamination_pass

            if cfg.is_Conversions == True:
                norm_cut  = self.hist_cfg.region.MC_contamination_pass

            if cfg.is_DY == True:
                # norm_cut  = self.hist_cfg.region.MC
                norm_cut  = self.hist_cfg.region.MC_contamination_pass

            if cfg.is_data == True:
                norm_cut  = self.hist_cfg.region.data

            if cfg.is_signal == True:
                norm_cut  = self.hist_cfg.region.signal

            if cfg.is_contamination == True:
                norm_cut  = self.hist_cfg.region.MC_contamination_fail
            
            weight = self.hist_cfg.weight
            if cfg.weight_expr:
                weight = '*'.join([weight, cfg.weight_expr])

            if 'disp1_0p5' in self.vcfgs[0].name:
                norm_cut += '&& (hnl_2d_disp < 0.5)'
            if 'disp1_2p0' in self.vcfgs[0].name:
                norm_cut += '&& (hnl_2d_disp < 2.0)'
            if 'disp2_0p5_10' in self.vcfgs[0].name:
                norm_cut += '&& ((hnl_2d_disp > 0.5) && (hnl_2d_disp < 10))'
            if 'disp2_2p0_10' in self.vcfgs[0].name:
                norm_cut += '&& ((hnl_2d_disp > 2.0) && (hnl_2d_disp < 10))'
            if 'disp3_10' in self.vcfgs[0].name:
                norm_cut += '&& hnl_2d_disp > 10'
            if 'disp2_0p5_5' in self.vcfgs[0].name:
                norm_cut += '&& ((hnl_2d_disp > 0.5) && (hnl_2d_disp < 5))'
            if 'disp3_5' in self.vcfgs[0].name:
                norm_cut += '&& hnl_2d_disp > 5'



            # Initialise all hists before the multidraw
            hists = {}

            for vcfg in self.vcfgs:
                # hname = '_'.join([self.hist_cfg.name, hashlib.md5(self.hist_cfg.cut).hexdigest(), cfg.name, vcfg.name, cfg.dir_name])
                hname = '_'.join([self.hist_cfg.name, hashlib.md5(norm_cut.encode('utf-8')).hexdigest(), cfg.name, vcfg.name, cfg.dir_name])
                if any(str(b) == 'xmin' for b in vcfg.binning):
                    hist = TH1F(hname, '', vcfg.binning['nbinsx'],
                                vcfg.binning['xmin'], vcfg.binning['xmax'])
                else:
                    hist = TH1F(hname, '', len(vcfg.binning['bins'])-1, vcfg.binning['bins'])

                initHist(hist, vcfg)
                hists[vcfg.name] = hist


            var_hist_tuples = []

            for vcfg in self.vcfgs:
                var_hist_tuples.append('{var} >> {hist}'.format(var=vcfg.drawname, hist=hists[vcfg.name].GetName()))

            stack = all_stack or (not cfg.is_data and not cfg.is_signal)


            # Produce all histograms using the RootDataFrame FW and add them to self.plots
            # print 'preparing %s with the following cut: '%(cfg.name) + norm_cut
            start = time.time()

            for vcfg in self.vcfgs:
                # self.makeDataFrameHistograms(vcfg,cfg,weight,dataframe,norm_cut,hists,stack)
                hist = self.makeDataFrameHistograms(vcfg,cfg,weight,dataframe,norm_cut,hists,stack,self.useNeuralNetwork)
                self.plots[vcfg.name].AddHistogram(cfg.name, hist.Clone(), stack=stack)

            # print('Added histograms for %s. It took %.1f secods'%(cfg.name,time.time()-start))
            PLOTS = self.plots
        return PLOTS

    def makeDataFrameHistograms(self,vcfg,cfg,weight,dataframe,norm_cut,hists,stack,useNeuralNetwork):
        plot = self.plots[vcfg.name]

        if (not cfg.is_data) and (not cfg.is_doublefake) and (not cfg.is_singlefake) and (not cfg.is_nonprompt):
            if cfg.is_reweightSignal:
                weight = weight + ' * ' + str(self.hist_cfg.lumi * cfg.xsec/cfg.sumweights)
            else:
                weight = weight + ' * ' + str(self.hist_cfg.lumi * cfg.xsec/cfg.sumweights)
            # if 'M_5_V_0p0007071067811865475' in cfg.name: set_trace()
            # if 'M_5_V_0p00145602197786' in cfg.name: set_trace()

        # gSystem.Load("modules/DDE_doublefake_h.so")
        # gSystem.Load("modules/DDE_singlefake_h.so")

        dataframe =   dataframe\
                                .Define('norm_count','1.')\
                                .Define('l0_pt_cone','l0_pt * (1 + l0_reliso_rho_03)')\
                                .Define('l1_ptcone','((l1_pt * (l1_reliso_rho_03<0.2)) + ((l1_reliso_rho_03>=0.2) * (l1_pt * (1. + l1_reliso_rho_03 - 0.2))))')\
                                .Define('l2_ptcone','((l2_pt * (l2_reliso_rho_03<0.2)) + ((l2_reliso_rho_03>=0.2) * (l2_pt * (1. + l2_reliso_rho_03 - 0.2))))')\
                                .Define('l1_ptcone_alt','(l1_pt * (1+l1_reliso_rho_03))')\
                                .Define('l2_ptcone_alt','(l2_pt * (1+l2_reliso_rho_03))')\
                                .Define('abs_dphi_01','abs(l1_phi-l0_phi)')\
                                .Define('abs_dphi_02','abs(l0_phi-l2_phi)')\
                                .Define('abs_dphi_hnvis0','abs(hnl_dphi_hnvis0)')\
                                .Define('abs_l1_Dz','abs(l1_dz)')\
                                .Define('abs_l2_Dz','abs(l2_dz)')\
                                # .Define('pt_cone','(  ( hnl_hn_vis_pt * (hnl_iso03_rel_rhoArea<0.2) ) + ( (hnl_iso03_rel_rhoArea>=0.2) * ( hnl_hn_vis_pt * (1. + hnl_iso03_rel_rhoArea - 0.2) ) )  )')\
                                # .Define('eta_hnl_l0','hnl_hn_eta - l0_eta')\
                                # .Define('abs_hnl_hn_eta','abs(hnl_hn_eta)')\
                                # .Define('abs_hnl_hn_vis_eta','abs(hnl_hn_vis_eta)')
                                # .Define('abs_l1_eta','abs(l1_eta)')\
                                # .Define('abs_l2_eta','abs(l2_eta)')\
                                # .Define('abs_l2_dxy','abs(l2_dxy)')\
                                # .Define('doubleFakeRate','dfr_namespace::getDoubleFakeRate(pt_cone, abs_hnl_hn_eta)')\
                                # .Define('doubleFakeRate','dfr_namespace::getDoubleFakeRate(pt_cone, abs_hnl_hn_eta, hnl_dr_12, hnl_2d_disp)')\
                                # .Define('singleFakeRate','sfr_namespace::getSingleFakeRate(pt_cone, abs_hnl_hn_eta)')\
        
        # define some extra columns for custom calculations
        if useNeuralNetwork == True:     
            if cfg.is_singlefake:
                dataframe =   dataframe\
                                        .Define('singleFakeRate1','SF1.ml_fr')\
                                        .Define('singleFakeWeight1','singleFakeRate1/(1.0-singleFakeRate1)')\
                                        .Define('singleFakeRate2','SF2.ml_fr')\
                                        .Define('singleFakeWeight2','singleFakeRate2/(1.0-singleFakeRate2)')
            if cfg.is_doublefake:
                dataframe =   dataframe\
                                        .Define('doubleFakeRate','DF.ml_fr')\
                                        .Define('doubleFakeWeight','doubleFakeRate/(1.0-doubleFakeRate)')
                                        # .Filter('doubleFakeRate != 1')\

            if cfg.is_nonprompt or cfg.is_contamination:
                dataframe =   dataframe\
                                        .Define('nonprompt_FakeRate','nonprompt.ml_fr')\
                                        .Define('nonprompt_FakeWeight','nonprompt_FakeRate/(1.0-nonprompt_FakeRate)')
            
        else:
            dataframe =   dataframe\
                                    .Define('singleFakeRate','sfr_namespace::getSingleFakeRate(pt_cone, abs_hnl_hn_eta)')\
                                    .Define('singleFakeWeight','singleFakeRate/(1.0-singleFakeRate)')\
                                    .Define('doubleFakeRate','dfr_namespace::getDoubleFakeRate(pt_cone, abs_hnl_hn_eta, hnl_dr_12, hnl_2d_disp)')\
                                    .Define('doubleFakeWeight','doubleFakeRate/(1.0-doubleFakeRate)')

        dataframe = dataframe\
                .Define('l1_ptcone_vs_pt','(l1_ptcone)/l1_pt')\
                .Define('l2_ptcone_vs_pt','(l2_ptcone)/l2_pt')\
                # .Define('m12Cone_vs_m12','(hnl_m_12_ConeCorrected2)/(hnl_m_12)')


        if cfg.is_singlefake:
            '''
            in this section we introduce singlefakes, which is made of the following components:
            1. tight prompt lepton + tight displaced lepton + loose-not-tight displaced lepton 
            - an application region for single fakes (SFR), these events are weighted 
            by SFR/(1-SFR) where SFR is taken for loose-not-tight displaced lepton;
            2. tight prompt lepton + two loose-not-tight displaced leptons where these displaced 
            leptons are not clustered into a single jet 
            - an application region for single FR, these events are weighted 
            by -SFR1/(1-SFR1)*SFR2/(1-SFR2).
            Note "-" sign: this contribution is subtracted from the contribution above (#2)
            '''

            dataframe =   dataframe\
                            .Define('weight_LL','(singleFakeWeight1 * singleFakeWeight2)')\
                            .Define('weight_LT','singleFakeWeight1')\
                            .Define('weight_TL','singleFakeWeight2')

            # dataframe =   dataframe\
                            # .Define('weight_LL','1')\
                            # .Define('weight_LT','1')\
                            # .Define('weight_TL','1')

            # # implement ptCone correction to the single fakes
            # if 'hnl_m_12' in vcfg.drawname:
                # vcfg.drawname = 'hnl_m_12_ConeCorrected'


            hist_sf_LL = dataframe\
                            .Filter(self.norm_cut_LL)\
                            .Histo1D((hists[vcfg.name].GetName(),'',vcfg.binning['nbinsx'],vcfg.binning['xmin'], vcfg.binning['xmax']),vcfg.drawname,'weight_LL')
            hist_sf_LL = hist_sf_LL.Clone() # convert the ROOT.ROOT::RDF::RResultPtr<TH1D> object into a ROOT.TH1D object

            hist_sf_LT = dataframe\
                            .Filter(self.norm_cut_LT)\
                            .Histo1D((hists[vcfg.name].GetName(),'',vcfg.binning['nbinsx'],vcfg.binning['xmin'], vcfg.binning['xmax']),vcfg.drawname,'weight_LT')
            hist_sf_LT = hist_sf_LT.Clone() # convert the ROOT.ROOT::RDF::RResultPtr<TH1D> object into a ROOT.TH1D object
        
            hist_sf_TL = dataframe\
                            .Filter(self.norm_cut_TL)\
                            .Histo1D((hists[vcfg.name].GetName(),'',vcfg.binning['nbinsx'],vcfg.binning['xmin'], vcfg.binning['xmax']),vcfg.drawname,'weight_TL')
            hist_sf_TL = hist_sf_TL.Clone() # convert the ROOT.ROOT::RDF::RResultPtr<TH1D> object into a ROOT.TH1D object


            hist_sf_TL.Add(hist_sf_LT)       
            hist_sf_TL.Add(hist_sf_LL,-1)       
            hists[vcfg.name] = hist_sf_TL      
            
            # hists[vcfg.name] = hist_sf_TL      
            # hists[vcfg.name] = hist_sf_LT      
            # hists[vcfg.name] = hist_sf_LL      
        
        if cfg.is_doublefake:
            '''
            in this section, we introduce the double fakes compoment:
            ==> tight prompt lepton + two loose-not-tight displaced leptons where these displaced 
            leptons are clustered into a single jet 
            - an application region for double FR, these events are weighted by DFR/(1-DFR) 
            where DFR is picked up as a function of a dilepton properties (pt-corr, eta, flavor).
            '''
            weight = 'doubleFakeWeight'

            is_corrupt = dataframe.Define('is_same','DF.hnl_hn_vis_pt - hnl_hn_vis_pt').Filter('is_same != 0').Count().GetValue()
            if is_corrupt > 0:
                print ('%s: main tree and friend tree do not match'%(cfg.name))
                set_trace()

        if cfg.is_nonprompt:
            '''
            This is a crazy attempt to have a single fake rate substituting both SF and DF.
            '''
            weight = 'nonprompt_FakeWeight'
            # is_corrupt = dataframe.Define('is_same','nonprompt.l2_pt - l2_pt').Filter('is_same != 0').Count().GetValue()
            # if is_corrupt > 0:
                # print '%s: main tree and friend tree do not match'%(cfg.name)
                # set_trace()
    
        if cfg.is_contamination:
            '''
            Eventually, the very same procedure of DDE should be applied to the MC samples,
            in order to remove prompt contamination from the application region. 
            In events taken from MC samples MC-truth matching should be always on
            (we need to pick up only prompt leptons from MC), and there the very
            same algorithm applies, but the sign of the contribution will be inverted:

            event weight for single FR: -SFR/(1-SFR)
            event weight for single FR with two fakes: SFR1/(1-SFR1)*SFR2/(1-SFR2)
            event weight for double FR: -DFR/(1-DFR)
            
            This signs inversion corresponds to the fact that we subtract from the data
            application region the prompt contamination (with MC truth matching and
            all the data/MC scale-factors applied).
            '''

            weight += '* (-1)'
            weight += '* nonprompt_FakeWeight'

        if ("TTJ" in cfg.name) or ("DY" in cfg.name):
            weight += '* l0_weight'

        if cfg.is_reweightSignal:
            # if   '0p00001'                    in cfg.name: weight += ' * ctau_w_v2_1em10   * xs_w_v2_1em10'
            # elif '0p000022360679774997898'    in cfg.name: weight += ' * ctau_w_v2_5em10   * xs_w_v2_5em10'
            # elif '0p000031622776601683795'    in cfg.name: weight += ' * ctau_w_v2_1em09   * xs_w_v2_1em09'
            # elif '0p00007071067811865475'     in cfg.name: weight += ' * ctau_w_v2_5em09   * xs_w_v2_5em09'
            # elif '0p0001'                     in cfg.name: weight += ' * ctau_w_v2_1em08   * xs_w_v2_1em08'    
            # elif '0p00022360679774997898'     in cfg.name: weight += ' * ctau_w_v2_5em08   * xs_w_v2_5em08'
            # elif '0p00031622776601683794'     in cfg.name: weight += ' * ctau_w_v2_1em07   * xs_w_v2_1em07'
            # elif '0p0007071067811865475'      in cfg.name: weight += ' * ctau_w_v2_5em07   * xs_w_v2_5em07'
            # elif '0p001'                      in cfg.name: weight += ' * ctau_w_v2_1em06   * xs_w_v2_1em06'
            # elif '0p00223606797749979'        in cfg.name: weight += ' * ctau_w_v2_5em06   * xs_w_v2_5em06'
            # elif '0p0024494897427831783'      in cfg.name: weight += ' * ctau_w_v2_6em06   * xs_w_v2_6em06'
            # elif '0p00282842712474619'        in cfg.name: weight += ' * ctau_w_v2_8em06   * xs_w_v2_8em06'
            # elif '0p0031622776601683794'      in cfg.name: weight += ' * ctau_w_v2_1em05   * xs_w_v2_1em05'
            # elif '0p00447213595499958'        in cfg.name: weight += ' * ctau_w_v2_2em05   * xs_w_v2_2em05'
            # elif '0p005477225575051661'       in cfg.name: weight += ' * ctau_w_v2_3em05   * xs_w_v2_3em05'
            # elif '0p006324555320336759'       in cfg.name: weight += ' * ctau_w_v2_4em05   * xs_w_v2_4em05'
            # elif '0p007071067811865475'       in cfg.name: weight += ' * ctau_w_v2_5em05   * xs_w_v2_5em05'
            # elif '0p008366600265340755'       in cfg.name: weight += ' * ctau_w_v2_7em05   * xs_w_v2_7em05'
            # elif '0p01'                       in cfg.name: weight += ' * ctau_w_v2_0.0001  * xs_w_v2_0.0001'
            # elif '0p01414213562373095'        in cfg.name: weight += ' * ctau_w_v2_0.0002  * xs_w_v2_0.0002'
            # elif '0p015811388300841896'       in cfg.name: weight += ' * ctau_w_v2_0.00025 * xs_w_v2_0.00025'
            # elif '0p017320508075688773'       in cfg.name: weight += ' * ctau_w_v2_0.0003  * xs_w_v2_0.0003'
            # elif '0p022360679774997897'       in cfg.name: weight += ' * ctau_w_v2_0.0005  * xs_w_v2_0.0005'
            # elif '0p034641016151377546'       in cfg.name: weight += ' * ctau_w_v2_0.0012  * xs_w_v2_0.0012'

            if   '0p00001'                      in cfg.name: weight += ' * ctau_w_v2_1p0em10  * xs_w_v2_1p0em10 '
            elif '0p00001414213562'             in cfg.name: weight += ' * ctau_w_v2_2p0em10  * xs_w_v2_2p0em10 '
            elif '0p00001732050808'             in cfg.name: weight += ' * ctau_w_v2_3p0em10  * xs_w_v2_3p0em10 '
            elif '0p00002'                      in cfg.name: weight += ' * ctau_w_v2_4p0em10  * xs_w_v2_4p0em10 '
            elif '0p000022360679774997898'      in cfg.name: weight += ' * ctau_w_v2_5p0em10  * xs_w_v2_5p0em10 '
            elif '0p00002449489743'             in cfg.name: weight += ' * ctau_w_v2_6p0em10  * xs_w_v2_6p0em10 '
            elif '0p00002645751311'             in cfg.name: weight += ' * ctau_w_v2_7p0em10  * xs_w_v2_7p0em10 '
            elif '0p00002828427125'             in cfg.name: weight += ' * ctau_w_v2_8p0em10  * xs_w_v2_8p0em10 '
            elif '0p00003'                      in cfg.name: weight += ' * ctau_w_v2_9p0em10  * xs_w_v2_9p0em10 '

            elif '0p000031622776601683795'      in cfg.name: weight += ' * ctau_w_v2_1p0em09  * xs_w_v2_1p0em09 '
            elif '0p00004472135955'             in cfg.name: weight += ' * ctau_w_v2_2p0em09  * xs_w_v2_2p0em09 '
            elif '0p00005477225575'             in cfg.name: weight += ' * ctau_w_v2_3p0em09  * xs_w_v2_3p0em09 '
            elif '0p0000632455532'              in cfg.name: weight += ' * ctau_w_v2_4p0em09  * xs_w_v2_4p0em09 '
            elif '0p00007071067811865475'       in cfg.name: weight += ' * ctau_w_v2_5p0em09  * xs_w_v2_5p0em09 '
            elif '0p00007745966692'             in cfg.name: weight += ' * ctau_w_v2_6p0em09  * xs_w_v2_6p0em09 '
            elif '0p00008366600265'             in cfg.name: weight += ' * ctau_w_v2_7p0em09  * xs_w_v2_7p0em09 '
            elif '0p0000894427191'              in cfg.name: weight += ' * ctau_w_v2_8p0em09  * xs_w_v2_8p0em09 '
            elif '0p00009486832981'             in cfg.name: weight += ' * ctau_w_v2_9p0em09  * xs_w_v2_9p0em09 '

            elif '0p0001'                       in cfg.name: weight += ' * ctau_w_v2_1p0em08  * xs_w_v2_1p0em08 '
            elif '0p0001414213562'              in cfg.name: weight += ' * ctau_w_v2_2p0em08  * xs_w_v2_2p0em08 '
            elif '0p0001732050808'              in cfg.name: weight += ' * ctau_w_v2_3p0em08  * xs_w_v2_3p0em08 '
            elif '0p0002'                       in cfg.name: weight += ' * ctau_w_v2_4p0em08  * xs_w_v2_4p0em08 '
            elif '0p00022360679774997898'       in cfg.name: weight += ' * ctau_w_v2_5p0em08  * xs_w_v2_5p0em08 '
            elif '0p0002449489743'              in cfg.name: weight += ' * ctau_w_v2_6p0em08  * xs_w_v2_6p0em08 '
            elif '0p0002645751311'              in cfg.name: weight += ' * ctau_w_v2_7p0em08  * xs_w_v2_7p0em08 '
            elif '0p0002828427125'              in cfg.name: weight += ' * ctau_w_v2_8p0em08  * xs_w_v2_8p0em08 '
            elif '0p0003'                       in cfg.name: weight += ' * ctau_w_v2_9p0em08  * xs_w_v2_9p0em08 '

            elif '0p00031622776601683795'       in cfg.name: weight += ' * ctau_w_v2_1p0em07  * xs_w_v2_1p0em07 '
            elif '0p0004472135955'              in cfg.name: weight += ' * ctau_w_v2_2p0em07  * xs_w_v2_2p0em07 '
            elif '0p0005477225575'              in cfg.name: weight += ' * ctau_w_v2_3p0em07  * xs_w_v2_3p0em07 '
            elif '0p000632455532'               in cfg.name: weight += ' * ctau_w_v2_4p0em07  * xs_w_v2_4p0em07 '
            elif '0p0007071067811865475'        in cfg.name: weight += ' * ctau_w_v2_5p0em07  * xs_w_v2_5p0em07 '
            elif '0p0007745966692'              in cfg.name: weight += ' * ctau_w_v2_6p0em07  * xs_w_v2_6p0em07 '
            elif '0p0008366600265'              in cfg.name: weight += ' * ctau_w_v2_7p0em07  * xs_w_v2_7p0em07 '
            elif '0p000894427191'               in cfg.name: weight += ' * ctau_w_v2_8p0em07  * xs_w_v2_8p0em07 '
            elif '0p0009486832981'              in cfg.name: weight += ' * ctau_w_v2_9p0em07  * xs_w_v2_9p0em07 '

            elif '0p001'                        in cfg.name: weight += ' * ctau_w_v2_1p0em06  * xs_w_v2_1p0em06 '
            elif '0p001414213562'               in cfg.name: weight += ' * ctau_w_v2_2p0em06  * xs_w_v2_2p0em06 '
            elif '0p001732050808'               in cfg.name: weight += ' * ctau_w_v2_3p0em06  * xs_w_v2_3p0em06 '
            elif '0p002'                        in cfg.name: weight += ' * ctau_w_v2_4p0em06  * xs_w_v2_4p0em06 '
            elif '0p0022360679774997898'        in cfg.name: weight += ' * ctau_w_v2_5p0em06  * xs_w_v2_5p0em06 '
            elif '0p002449489743'               in cfg.name: weight += ' * ctau_w_v2_6p0em06  * xs_w_v2_6p0em06 '
            elif '0p002645751311'               in cfg.name: weight += ' * ctau_w_v2_7p0em06  * xs_w_v2_7p0em06 '
            elif '0p002828427125'               in cfg.name: weight += ' * ctau_w_v2_8p0em06  * xs_w_v2_8p0em06 '
            elif '0p003'                        in cfg.name: weight += ' * ctau_w_v2_9p0em06  * xs_w_v2_9p0em06 '

            elif '0p0031622776601683795'        in cfg.name: weight += ' * ctau_w_v2_1p0em05  * xs_w_v2_1p0em05 '
            elif '0p004472135955'               in cfg.name: weight += ' * ctau_w_v2_2p0em05  * xs_w_v2_2p0em05 '
            elif '0p005477225575'               in cfg.name: weight += ' * ctau_w_v2_3p0em05  * xs_w_v2_3p0em05 '
            elif '0p00632455532'                in cfg.name: weight += ' * ctau_w_v2_4p0em05  * xs_w_v2_4p0em05 '
            elif '0p007071067811865475'         in cfg.name: weight += ' * ctau_w_v2_5p0em05  * xs_w_v2_5p0em05 '
            elif '0p007745966692'               in cfg.name: weight += ' * ctau_w_v2_6p0em05  * xs_w_v2_6p0em05 '
            elif '0p008366600265'               in cfg.name: weight += ' * ctau_w_v2_7p0em05  * xs_w_v2_7p0em05 '
            elif '0p00894427191'                in cfg.name: weight += ' * ctau_w_v2_8p0em05  * xs_w_v2_8p0em05 '
            elif '0p009486832981'               in cfg.name: weight += ' * ctau_w_v2_9p0em05  * xs_w_v2_9p0em05 '

            elif '0p01'                         in cfg.name: weight += ' * ctau_w_v2_1p0em04  * xs_w_v2_1p0em04 '
            elif '0p01414213562'                in cfg.name: weight += ' * ctau_w_v2_2p0em04  * xs_w_v2_2p0em04 '
            elif '0p01732050808'                in cfg.name: weight += ' * ctau_w_v2_3p0em04  * xs_w_v2_3p0em04 '
            elif '0p02'                         in cfg.name: weight += ' * ctau_w_v2_4p0em04  * xs_w_v2_4p0em04 '
            elif '0p022360679774997898'         in cfg.name: weight += ' * ctau_w_v2_5p0em04  * xs_w_v2_5p0em04 '
            elif '0p02449489743'                in cfg.name: weight += ' * ctau_w_v2_6p0em04  * xs_w_v2_6p0em04 '
            elif '0p02645751311'                in cfg.name: weight += ' * ctau_w_v2_7p0em04  * xs_w_v2_7p0em04 '
            elif '0p02828427125'                in cfg.name: weight += ' * ctau_w_v2_8p0em04  * xs_w_v2_8p0em04 '
            elif '0p03'                         in cfg.name: weight += ' * ctau_w_v2_9p0em04  * xs_w_v2_9p0em04 '

            elif '0p031622776601683795'         in cfg.name: weight += ' * ctau_w_v2_1p0em03  * xs_w_v2_1p0em03 '
            elif '0p04472135955'                in cfg.name: weight += ' * ctau_w_v2_2p0em03  * xs_w_v2_2p0em03 '
            elif '0p05477225575'                in cfg.name: weight += ' * ctau_w_v2_3p0em03  * xs_w_v2_3p0em03 '
            elif '0p0632455532'                 in cfg.name: weight += ' * ctau_w_v2_4p0em03  * xs_w_v2_4p0em03 '
            elif '0p07071067811865475'          in cfg.name: weight += ' * ctau_w_v2_5p0em03  * xs_w_v2_5p0em03 '
            elif '0p07745966692'                in cfg.name: weight += ' * ctau_w_v2_6p0em03  * xs_w_v2_6p0em03 '
            elif '0p08366600265'                in cfg.name: weight += ' * ctau_w_v2_7p0em03  * xs_w_v2_7p0em03 '
            elif '0p0894427191'                 in cfg.name: weight += ' * ctau_w_v2_8p0em03  * xs_w_v2_8p0em03 '
            elif '0p09486832981'                in cfg.name: weight += ' * ctau_w_v2_9p0em03  * xs_w_v2_9p0em03 '

            elif '0p1'                          in cfg.name: weight += ' * ctau_w_v2_1p0em02  * xs_w_v2_1p0em02 '
            elif '0p1414213562'                 in cfg.name: weight += ' * ctau_w_v2_2p0em02  * xs_w_v2_2p0em02 '
            elif '0p1732050808'                 in cfg.name: weight += ' * ctau_w_v2_3p0em02  * xs_w_v2_3p0em02 '
            elif '0p2'                          in cfg.name: weight += ' * ctau_w_v2_4p0em02  * xs_w_v2_4p0em02 '
            elif '0p22360679774997898'          in cfg.name: weight += ' * ctau_w_v2_5p0em02  * xs_w_v2_5p0em02 '
            elif '0p2449489743'                 in cfg.name: weight += ' * ctau_w_v2_6p0em02  * xs_w_v2_6p0em02 '
            elif '0p2645751311'                 in cfg.name: weight += ' * ctau_w_v2_7p0em02  * xs_w_v2_7p0em02 '
            elif '0p2828427125'                 in cfg.name: weight += ' * ctau_w_v2_8p0em02  * xs_w_v2_8p0em02 '
            elif '0p3'                          in cfg.name: weight += ' * ctau_w_v2_9p0em02  * xs_w_v2_9p0em02 '

            elif '0p31622776601683795'          in cfg.name: weight += ' * ctau_w_v2_1p0em01  * xs_w_v2_1p0em01 '
            elif '0p4472135955'                 in cfg.name: weight += ' * ctau_w_v2_2p0em01  * xs_w_v2_2p0em01 '
            elif '0p5477225575'                 in cfg.name: weight += ' * ctau_w_v2_3p0em01  * xs_w_v2_3p0em01 '
            elif '0p632455532'                  in cfg.name: weight += ' * ctau_w_v2_4p0em01  * xs_w_v2_4p0em01 '
            elif '0p7071067811865475'           in cfg.name: weight += ' * ctau_w_v2_5p0em01  * xs_w_v2_5p0em01 '
            elif '0p7745966692'                 in cfg.name: weight += ' * ctau_w_v2_6p0em01  * xs_w_v2_6p0em01 '
            elif '0p8366600265'                 in cfg.name: weight += ' * ctau_w_v2_7p0em01  * xs_w_v2_7p0em01 '
            elif '0p894427191'                  in cfg.name: weight += ' * ctau_w_v2_8p0em01  * xs_w_v2_8p0em01 '
            elif '0p9486832981'                 in cfg.name: weight += ' * ctau_w_v2_9p0em01  * xs_w_v2_9p0em01 '

            else: set_trace()

        
        if not cfg.is_singlefake:
            # if 'A' in cfg.name: set_trace()
	    # if 'Single' in cfg.name: set_trace()
            # if 'M_5_V_0p00145602197786' in cfg.name: set_trace()
            try:
                if 'nbinsx' in vcfg.binning.keys():
                    hists[vcfg.name] =   dataframe\
                                            .Define('w',weight)\
                                            .Filter(norm_cut)\
                                            .Histo1D((hists[vcfg.name].GetName(),'',vcfg.binning['nbinsx'],vcfg.binning['xmin'], vcfg.binning['xmax']),vcfg.drawname,'w')
                else: #if custom bins are give (e.g. log bins)
                    hists[vcfg.name] =   dataframe\
                                            .Define('w',weight)\
                                            .Filter(norm_cut)\
                                            .Histo1D((hists[vcfg.name].GetName(),'',len(vcfg.binning['bins'])-1,vcfg.binning['bins']),vcfg.drawname,'w')
            except: set_trace()

            histo = hists[vcfg.name]
        return hists[vcfg.name]

