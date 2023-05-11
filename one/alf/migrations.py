# -*- coding: utf-8 -*-
"""
Created on Wed Feb 15 11:05:19 2023

@author: tjostmou
"""

import os, re

from . import spec, files

VGAT_MOUSE_NO = r"Vgat.*?(\d{1,3}).*jRGECO"
"""A pattern to extract the mouse number from old tdms Vgat files"""

TIFF_FRAME_NO = r".*(\d{5})\.tiff?$"
"""A pattern to extract trial number from tiff files"""

TDMS_TRIALS_TABLE_TIME = r"(\d{4}_\d{2}_\d{2}_\d{2}_\d{2})\.tdms$"
"""Extract time from tdms file name"""

TDMS_VIDEO_TRIAL_NO = r".*?(\d+)\.tdms$"
"""Extract trial number from tdms video file name"""

FOV_NO = r"^.*?(?:FOV)?(\d+).*\.(?:(?:PNG)|(?:png))$"
"""Extract field of view number from a png file"""

CONDENSED_DATE = r".*?(20\d{4,6}).*?"
"""Get condensed date"""

SESSION_NO = r".*?20\d{4,6}_(\d).*?"
"""Extract session number from a path next to a date"""

ALYX_PATH_EID = r"\w+(?:\\|\/)\d{4}\-\d{2}\-\d{2}(?:\\|\/)\d{3}"
"""Match a unique session id (human readable) like wm25/2022-08-08/001"""


def extract_extra_from_non_alfpath(filename, re_pattern):
    results = []
    matches = re.finditer(re_pattern, filename, re.MULTILINE|re.IGNORECASE)
    
    for matchnum, match in enumerate(matches,  start = 1):
        for groupx, groupmatch in enumerate(match.groups()):
            results.append(groupmatch)
    return results

def apply_name_change(src_list,new_list):
    assert len(src_list) == len(new_list), "Length of lists must match for name conversion"
    
    for src_file , new_file in zip(src_list,new_list) :
        if new_file == '':
            continue
        os.rename(src_file,new_file)
        

def rename_file(filename, alf_info):
    
    alf_info = files
    
    def _finish_file():
        return spec.to_alf(alf_info['object'],
                                   alf_info['attribute'],
                                   alf_info['extension'],
                                   namespace = alf_info['namespace'],
                                   timescale = alf_info['timescale'],
                                   extra = alf_info['extra'])
    
    def get_imaging_type(collec):
        imaging_types = ("pupil",)
        
        for i_type in imaging_types :
            if i_type in collec :
                return i_type
        return None
    
    alf_info["timescale"] = None
    alf_info["namespace"] = None
    alf_info["revision"] = None
    
    if (alf_info["extension"] == "tif" or alf_info["extension"] == 'tiff') :
        alf_info["object"] = "imaging"
        
        if alf_info["collection"] is not None :
            if "imaging_data" in alf_info["collection"] :
                trial_no = extract_extra_from_non_alfpath(filename, TIFF_FRAME_NO)
                
                if trial_no :
                    alf_info["extra"] = trial_no[0].zfill(5)
                    alf_info["attribute"] = "frames"
                    return _finish_file()
            
    elif alf_info["extension"] == "tdms" :
        
        if alf_info["collection"] is None :
            alf_info["object"] = "trials"
            alf_info["attribute"] = "eventTimeline"
            return _finish_file()
        
        elif "behaviour_imaging" in alf_info["collection"] :
            alf_info["object"] = "behaviour"
            alf_info["attribute"] = "video"
            i_type = get_imaging_type(alf_info["collection"])
            
            if i_type is not None :
                alf_info["extra"] = i_type
                trial_no = extract_extra_from_non_alfpath(filename, TDMS_VIDEO_TRIAL_NO)
                
                if trial_no :
                    alf_info["extra"] = append_extra(trial_no[0].zfill(5) ,alf_info["extra"])
                    return _finish_file()
        
    elif alf_info["extension"] == "PNG" or alf_info["extension"] == 'png' :
        alf_info["extension"] = 'png' #enforce lower caps
        FOV_no = extract_extra_from_non_alfpath(filename, FOV_NO)
        
        if FOV_no :
            alf_info["object"] = "imaging"
            alf_info["attribute"] = "fieldOfView"
            alf_info["extra"] = FOV_no[0].zfill(2)
            return _finish_file()

    return None #if none of the renaming directives returned a results above, we don't rename

    
    