from master.plot_cfg_hn3l import *

# from CMGTools.HNL.plot_cfg_hn3l_old import *

promptLeptonType = "mu" # do "ele" or "mu"
L1L2LeptonType   = "mm" # do "mm", "me", "ee"
server           = "starseeker" # do "t3" or "lxplus" or "starseeker"

# producePlots(promptLeptonType = promptLeptonType, L1L2LeptonType = L1L2LeptonType)
producePlots(promptLeptonType, L1L2LeptonType, server)
