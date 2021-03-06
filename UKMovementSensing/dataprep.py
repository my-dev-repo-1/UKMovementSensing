from __future__ import print_function
from __future__ import division
from builtins import zip
from builtins import str
from builtins import range
from past.utils import old_div

import pandas as pd
import dateutil.parser
import numpy as np
import datetime
import os


def parse_time(date_string):
    try:
        return pd.to_datetime(date_string, utc=True).tz_convert('Europe/London')
    except:
        print('Could not parse: ', date_string)
        return None


def remove_invalid_annotations(annotations):
    # TODO: we can remove this function if we no longer have missing dates
    # Remove entries with missing start or endtime
    starttime_missing = annotations['start_time'].isnull()
    endtime_missing = annotations['end_time'].isnull()
    print('Number of missing start and end times:', sum(starttime_missing), sum(endtime_missing))
    keep = np.logical_not(np.logical_or(starttime_missing, endtime_missing))
    annotations_valid = annotations[keep]
    return annotations_valid


def process_annotations(annotations_path):
    """
    Read the time use diary (TUD) annotations file as stored in csv format
    Parameters
    ----------
    annotations_path : str
        Path to TUD file

    Returns
    -------
    Pandas dataframe with annotations

    """
    annotations = pd.read_csv(annotations_path)
    annotations = remove_invalid_annotations(annotations)

    # Convert timestamps to datetime
    annotations['start_time'] = [parse_time(s) for s in annotations['start_time']]
    annotations['end_time'] = [parse_time(s) for s in annotations['end_time']]

    # Sort on measure day and time slot
    annotations = annotations.sort_values(['accSmallID', 'day', 'slot'])
    annotations.index = list(range(annotations.shape[0]))

    # Check if the differences between start and end is always 10 minutes
    differences = [x - y for x,y in zip(annotations['end_time'], annotations['start_time'])]
    diff_indices = [x != pd.Timedelta(10, unit='m') for x in differences]
    if sum(diff_indices) > 0:
        # TODO: this should be unnecessary!
        print('Differens between start and end not always 10 minutes! in %d cases. First 10:' % sum(diff_indices))
        print(annotations[diff_indices][['start_time', 'end_time']].head(10))
        annotations.loc[diff_indices, 'end_time'] = [pd.Timedelta(10, unit='m') + x for x in annotations[diff_indices]['start_time']]

    # Check if every timeslot is associated with exactly one time
    annotations['end_time_time'] = [s.time() for s in annotations['end_time']]
    byslot_endtime = annotations.groupby(['slot']).end_time_time
    multiple_endtime = (byslot_endtime.nunique() > 1)
    if sum(multiple_endtime)>0:
        print('Multiple end times per slot. first 10:')
        print(byslot_endtime.unique()[multiple_endtime.index].head(10))
    annotations.drop('end_time_time', 1, inplace=True)

    # Check if each day has exactly 144 time slots
    nrslots = annotations.groupby(['accSmallID', 'day']).slot.nunique()
    if not np.alltrue(nrslots == 144):
        print('Not all days have 144 slots! first 5:')
        print(nrslots[nrslots!=144].head())

    return annotations


def join_wearcodes(wearcodes_path, annotations):
    """
    Joins the annotations with the wearcodes. The wearcodes are the dates on
    which an accelerometer was supposed to be worn

    Parameters
    ----------
    wearcodes_path : str
        path to the file with wearcodes
    annotations : pandas dataframe
        Dataframe with the annotations (output from process_annotations)

    Returns
    -------
    Pandas Dataframe with annotations and wearcodes

    """

    # Read in the wearcodes
    wearcodes = pd.read_csv(wearcodes_path)
    wearcodes['Day1'] = [datetime.datetime.strptime(s, '%d/%m/%Y') for s in wearcodes['Day1']]
    wearcodes['Day2'] = [datetime.datetime.strptime(s, '%d/%m/%Y') for s in wearcodes['Day2']]

    # Join with annotations
    annotations_codes = pd.merge(annotations, wearcodes, on='accSmallID', how='left')

    # Check if all wearcodes are present
    if(sum(annotations_codes['Monitor'] == None) > 0):
        print('Some accSmallIDs are not present! First 5:')
        print(annotations_codes[annotations_codes['Monitor'] == None].head())

    return annotations_codes


def load_data(binfile, day, filepath):
    """
    Loads the (5-second aggregated) accelerometer data.
    The path to the file is: `[filepath]/[binfile]_[day].csv`
    Here, binfile refers to the binary accelerometer data data from which
    the 5-second accelerometer data (csv) was created.

    Parameters
    ----------
    binfile : str
        Name of the original binary file (from wearcodes)
    day : int
        Day (1 or 2)
    filepath
        directory where the file is located

    Returns
    -------
    Pandas Dataframe with accelerometer data

    """
    result = None
    filename_csv = binfile + '_day' + str(day) + '.csv'
    filename = filepath + filename_csv
    try:
        data = pd.read_csv(filename,
                           index_col='timestamp', parse_dates=[0],
                           infer_datetime_format=True)
        data.index = data.index.tz_localize('Europe/London')
        data = data.dropna()
        data['filename'] = binfile + '_day' + str(day)
        result = data
    except:
        print('File not found: ', filename_csv)
        return None
    return result


def add_annotations(df, binfile, day, annotations_group):
    """
    Add annotations to accelerometer data.

    Parameters
    ----------
    df : Pandas DataFrame
        Accelerometer data
    binfile : str
        Name of original binary file (from wearcodes)
    day
        Day (1 or 2)
    annotations_group
        Annotations for this specific binfile and day

    Returns
    -------
    Pandas DataFrame with accelerometer data and annotations.
    """
    firsttime = df.index[0]
    # Check if start time of annotations corresponds with start time of accelerometer data
    if firsttime != min(annotations_group.start_time): #firsttime.tz_localize('UTC') != min(annotations_group.start_time) :
       print('starttime of data does not correspond with starttime of annotations!')
       print(firsttime, min(annotations_group.start_time))
    # Add slot column to df
    df['slot'] = df.index - firsttime
    df['slot'] = [int(np.floor(old_div(s.total_seconds(), 600))) + 1 for s in df['slot']]
    if(len(df.slot.unique())<144):
        print('Warning: only %d slots'%len(df.slot.unique()))
    df_annotated = pd.merge(df, annotations_group[['slot', 'activity', 'label', 'start_time']], on='slot', how='left')
    df_annotated.index = df.index
    return df_annotated


def process_data(annotations_codes, filepath):
    """
    Load all accelerometer data in the specified directory,
    and join with annotations.

    Parameters
    ----------
    annotations_codes : Pandas DataFrame
        DataFrame with annotations and wearcodes (result from join_wearcodes)
    filepath : str
        Directory of the accelerometer data files

    Returns
    -------
    dict holding all the merged dataframe
    """
    byName = annotations_codes.groupby(['binFile', 'day'])
    dfs = {}
    for (binfile, day), fileAnnotations in byName:
        annotations_group = byName.get_group((binfile, day))
        # Load data
        df = load_data(binfile, day, filepath)

        if df is not None:
            # Add annotations:
            df = add_annotations(df, binfile, day, annotations_group)
            # Keep all data frames
            dfs[(binfile, day)] = df
    return dfs


def process_data_onebyone(annotations_codes, filepath,  subsets_path):
    """
    Process the accelerometer files one-by-one.
    Joins them with the annotations, creates subsequences, switches position
    and writes the data to the output path defined in argument subsets_path.

    Parameters
    ----------
    annotations_codes : Pandas DataFrame
        Annotations joined with wearcodes (output from join_wearcodes)
    filepath
    subsets_path
    """
    byName = annotations_codes.groupby(['binFile', 'day'])
    for (binfile, day), fileAnnotations in byName:
        dfs = {}
        annotations_group = byName.get_group((binfile, day))

        # Load data
        df = load_data(binfile, day, filepath)
        if df is not None:
            # Add annotations:
            df = add_annotations(df, binfile, day, annotations_group)
            # Keep all data frames
            dfs[(binfile, day)] = df

            # Take subsequences
            subsets = take_subsequences(dfs)

            # Switch positions
            subsets_switched = switch_positions(subsets)

            # Save file
            save_subsequences(subsets, subsets_path)


def save_merged(dfs, merged_path):
    """
    Save the merged data.

    Parameters
    ----------
    dfs : dict
        dict holding all the merged dataframes (result from process_data)
    merged_path : str
        directory to store the files
    """
    if not os.path.exists(merged_path):
        os.makedirs(merged_path)
    for binfile, day in list(dfs.keys()):
        df = dfs[(binfile, day)]
        df.to_csv(os.path.join(merged_path, binfile + '_day' + str(day) + '.csv'))


def take_subsequences(dfs):
    """
    Make subsequences of the data that are completely valid.

    Parameters
    ----------
    dfs : dict
        dict holding all the merged dataframes (result from process_data)

    Returns
    -------
    dict holding all the subsequences

    """
    subsets = {}
    for binfile, day in list(dfs.keys()):
        dataset = dfs[(binfile, day)]
        invalids = [1] + list(dataset['invalid']) + [1]
        starts = [i for i in range(1, len(invalids) - 1) if invalids[i - 1] == 1 and invalids[i] == 0]
        ends = [i for i in range(1, len(invalids)) if invalids[i - 1] == 0 and invalids[i] == 1]
        dataset['subset'] = -1
        for i, (s, e) in enumerate(zip(starts, ends)):
            # Some minimum length
            if e - s > 300:
                dataset.loc[s - 1:e - 1, 'subset'] = i
                subsets[(binfile, day, i)] = (dataset[s - 1:e - 1].copy())
    return subsets


def save_subsequences(subsets, subsets_path):
    """
    Save the subsequences to the path: `[subsets_path]/[filename].csv`

    Parameters
    ----------
    subsets : dict
        Dict holding the dataframes of the subsequences
    subsets_path : str
        Directory to store the outputs
    """
    if not os.path.exists(subsets_path):
        os.makedirs(subsets_path)
    for i, dat in enumerate(subsets.values()):
        fn = str(str(dat['subset'][0]) + dat['filename'][0]) + '.csv'
        dat.to_csv(os.path.join(subsets_path, fn))


def switch_positions(dfs):
    """
    Check the orientation position, and switch if necessary.
    The orientation is switched if the median of the x-angle is larger than 0.
    In this case, the x-angle and y-angle are mirrored.

    Parameters
    ----------
    dfs : dict
        Dict holding the dataframes of the sequences

    Returns
    -------
    Switched Dataframes

    """
    switch_columns = ['anglex', 'angley', 'roll_med_acc_x', 'roll_med_acc_y', 'dev_roll_med_acc_x',
                      'dev_roll_med_acc_y']
    for dataset in list(dfs.values()):
        dataset['switched_pos'] = False
        if 'activity' in dataset.columns:
            non_sleeping_indices = dataset['activity'] != 1.0
            non_sleeping = dataset[non_sleeping_indices]
        else:
           non_sleeping = dataset
        if not non_sleeping.empty:
            # in the 'correct' orientation, anglex should be mostly negative
            med_x = np.median(non_sleeping['anglex'])
            if med_x > 0:
                for c in switch_columns:
                    if c in list(dataset.keys()):
                        dataset[c] *= -1
                dataset['switched_pos'] = True
                print('switched dataset with median %f'%med_x)
    return dfs


if __name__ == "__main__":
    import sys
    file_path = sys.argv[1]
    annotations_path = sys.argv[2]
    wearcodes_path = sys.argv[3]
    if len(sys.argv)>4:
        output_path = sys.argv[4]
    else:
        output_path = os.path.join(file_path)
    print('Process annotations...')
    annotations = process_annotations(annotations_path)
    print('Join wearcodes...')
    annotations_codes = join_wearcodes(wearcodes_path, annotations)
    print('Process data...')
    dfs = process_data(annotations_codes, file_path)
    print('Save merged...')
    save_merged(dfs, os.path.join(output_path, 'merged/'))
    print('Take subsequences...')
    subsets = take_subsequences(dfs)
    print('Switch positions...')
    subsets = switch_positions(subsets)
    print('Save subsequences...')
    save_subsequences(subsets, os.path.join(output_path, 'subsets/'))
