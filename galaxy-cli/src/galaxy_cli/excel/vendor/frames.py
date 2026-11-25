"""Adapted from mbeps/excel-mcp merge_datasets/filter_data_advanced (MIT)."""
import pandas as pd


def merge_frames(left, right, left_on, right_on):
    # Upstream uses pd.merge; Galaxy additionally requires many-to-one cardinality.
    return pd.merge(left, right, left_on=left_on, right_on=right_on,
                    how='left', sort=False, validate='many_to_one')


def combine_masks(masks, logic):
    combined = masks[0]
    for mask in masks[1:]:
        combined = (combined & mask) if logic == 'AND' else (combined | mask)
    return combined
