import pandas as pd
import numpy as np
import scipy.stats as st
import os, subprocess
import shutil
from collections import Counter

from .tools import *

def remove_na(data):
    """Identify the columns containing NA values. Delete rows with NA values."""
    nrows = data.shape[0]
    columns_na = data.columns[data.isna().any()].tolist()
    data.dropna(inplace=True)
    n_del = nrows - data.shape[0]
    if n_del>0:
        print(f"Deleted {n_del}({n_del/nrows*100:.3f}%) rows containing NA values in columns {columns_na}. Use preprocessing = 1 to keep the rows containing NaN values.")
    return data

def check_snp_column(data):
    """Remove duplicates in the SNP column."""
    duplicates = data.duplicated(subset=["SNP"], keep="first")
    data = data[~duplicates]
    n_del = duplicates.sum()
    if n_del > 0:
        print(f"{n_del}({n_del/data.shape[0]*100:.3f}%) duplicated SNPs have been removed. Use keep_dups=True to keep them.")  
    return data

def check_allele_column(data, allele_col, keep_multi):
    """
    Verify that the corresponding allele column is upper case strings. Set to nan if not formed with A, T, C, G letters. 
    Set to nan if values are multiallelic unless keep_multi=True.
    """
    nrows = data.shape[0]
    data[allele_col] = data[allele_col].astype(str).str.upper()
    atcg_condition = data[allele_col].str.contains('^[ATCG]+$', na=False)
    atcg_count = nrows - atcg_condition.sum()
    if atcg_count>0:
        data.loc[~atcg_condition, allele_col] = np.nan
        print(f"{atcg_count}({atcg_count/nrows*100:.3f}%) rows contain non A, T, C, G values in the {allele_col} column and are set to nan.")
    if not keep_multi:
        nrows = data.shape[0]
        multi_condition = (data[allele_col].str.len()>1)
        multi_count = multi_condition.sum()
        if multi_count > 0:
            data.loc[multi_condition, allele_col] = np.nan
            print(f"{multi_count}({multi_count/nrows*100:.3f}%) rows containing multiallelic values in the {allele_col} column and are set to nan. Use keep_multi=True to keep them.")
    return data

def fill_se_p(data):
    """If either P or SE is missing but the other and BETA are present, fill it."""
    # If SE is missing
    if (("P" in data.columns) & ("BETA" in data.columns) & ("SE" not in data.columns)):
        data["SE"] = np.where(data["P"]<1, 
                                   np.abs(data.BETA/st.norm.ppf(data.P/2)), 
                                   0)
        print("The SE (Standard Error) column has been created.")
    # If P is missing
    if (("SE" in data.columns) & ("BETA" in data.columns) & ("P" not in data.columns)):
        data["P"] = np.where(data["SE"]>0, 2*(1-st.norm.cdf(np.abs(data.BETA)/data.SE)), 1)
        print("The P (P-value) column has been created.")
    return data

def check_p_column(data):
    """Verify that the P column contains numeric values in the range [0,1]. Set inappropriate values to NA."""
    nrows = data.shape[0]
    data["P"] = pd.to_numeric(data["P"], errors="coerce")
    data.loc[(data['P'] < 0) & (data['P'] > 1)] = np.nan
    n_missing = data["P"].isna().sum()
    if n_missing > 0:
        print(f"{n_missing}({n_missing/nrows*100:.3f}%) values in the P column have been set to nan for being missing, non numeric or out of range [0,1].")
    return data

def check_beta_column(data, effect_column, preprocessing):
    """
    If the BETA column is a column of odds ratios, log-transform it.
    If no effect_column argument is specified, determine if the BETA column are beta estimates or odds ratios.
    """
    if effect_column is None:
        if preprocessing == 0:
            return data
        median=np.median(data.BETA)
        if 0.5 < median < 1.5:
            effect_column="OR"
            print("The BETA column looks like Odds Ratios. Use effect_column='BETA' if it is a column of Beta estimates.")
        else:
            effect_column="BETA"
            print("The BETA column looks like Beta estimates. Use effect_column='OR' if it is a column of Odds Ratios.")

    ## Log transform the effect column if appropriate
    if effect_column not in ["BETA","OR"]:
        raise ValueError("The argument effect_column accepts only 'BETA' or 'OR' as values.")
    if effect_column=="OR":
        data["BETA"]=np.log(data["BETA"])
        data.drop(columns="SE", errors="ignore", inplace=True)
        print("The BETA column has been log-transformed to obtain Beta estimates.")
    return data

def fill_ea_nea(data, reference_panel_df):
    """Fill in the EA and NEA columns based on reference data."""
    if "BETA" in data.columns:
        print("Warning: You have specified an effect (BETA) column but no effect allele (EA) column. An effect estimate is only meaningful if paired with its corresponding allele.")
    data = data.merge(reference_panel_df[["CHR","POS","A1","A2"]],on=["CHR","POS"],how="left")
    n_missing = data["A1"].isna().sum()
    data.rename(columns={"A1":"EA","A2":"NEA"}, inplace=True)
    print(f"Alleles columns created: effect (EA) and non-effect allele (NEA). {n_missing}({n_missing/data.shape[0]*100:.3f}%) values are set to nan because SNPs were not found in the reference data.")
    return data

def fill_nea(data, reference_panel_df):
    """Fill in the NEA column based on reference data."""
    data = data.merge(reference_panel_df[["CHR","POS","A1","A2"]], on=["CHR","POS"], how="left")
    conditions = [
        data["EA"] == data["A1"],
        data["EA"] == data["A2"]]
    choices = [data["A2"], data["A1"]]
    data["NEA"] = np.select(conditions, choices, default=np.nan)
    n_missing = data["NEA"].isna().sum()
    data.drop(columns=["A1","A2"], inplace=True)
    print(f"The NEA (Non Effect Allele) column has been created. {n_missing}({n_missing/data.shape[0]*100:.3f}%) values are set to nan because SNPs were not found in the reference data.")
    return data

def fill_coordinates_func(data, reference_panel_df):
    """Fill in the CHR/POS columns based on reference data."""
    if not "SNP" in data.columns:
        raise ValueError(f"The SNP column is not found in the data and is mandatory to fill coordinates!")
    data.drop(columns=["CHR", "POS"], inplace=True, errors="ignore")
    data = data.merge(reference_panel_df[["CHR","POS","SNP"]],on="SNP",how="left")
    n_missing = data["CHR"].isna().sum()
    data["CHR"] = data["CHR"].astype('Int32')
    data["POS"] = data["POS"].astype('Int32')
    print(f"The coordinates columns (CHR for chromosome and POS for position) have been created. {n_missing}({n_missing/data.shape[0]*100:.3f}%) SNPs were not found in the reference data and their values  set to nan.") 
    return data

def fill_snpids_func(data, reference_panel_df):
    """
    Fill in the SNP column based on reference data.
    If some SNPids are still missing, they will be replaced by a standard name: CHR:POS:EA 
    """
    for column in ["CHR","POS"]:
        if not(column in data.columns):
            raise ValueError(f"The column {column} is not found in the data and is mandatory to fill snpID!")
    data.drop(columns=["SNP"], inplace=True, errors="ignore")
    data = data.merge(reference_panel_df[["CHR","POS","SNP"]], on=["CHR","POS"], how="left")
    n_missing = data["SNP"].isna().sum()
    
    standard_name_condition = "EA" in data.columns and n_missing > 0
    if standard_name_condition:
        missing_snp_condition = data["SNP"].isna()
        data.loc[missing_snp_condition, "SNP"] = (
            data.loc[missing_snp_condition, "CHR"].astype(str) + ":" + 
            data.loc[missing_snp_condition, "POS"].astype(str) + ":" + 
            data.loc[missing_snp_condition, "EA"].astype(str)
        )
        print_statement = f" and their name set to CHR:POS:EA."
        
    print(f"The SNP column (rsID) has been created. {n_missing}({n_missing/data.shape[0]*100:.3f}%) SNPs were not found in the reference data{print_statement if standard_name_condition else ''}.")
    return data

def check_int_column(data, int_col):
    """Set the type of the int_col column to Int32 and non-numeric values to NA."""
    nrows = data.shape[0]
    data[int_col] = pd.to_numeric(data[int_col], errors="coerce")
    data[int_col] = data[int_col].round(0).astype('Int32')
    n_nan = data[int_col].isna().sum()
    if n_nan > 0:
        print(f"The {int_col} column contains {n_nan}({n_nan/nrows*100:.3f}%) values set to NaN (due to being missing or non-integer).")
    return data

def adjust_column_names(data, CHR, POS, SNP, EA, NEA, BETA, SE, P, EAF, keep_columns):
    """
    Rename columns to the standard names making sure that there are no duplicated names.
    Delete other columns if keep_columns=False, keep them if True.
    """
    rename_dict = {CHR:"CHR", POS:"POS", SNP:"SNP", EA:"EA", NEA:"NEA", BETA:"BETA", SE:"SE", P:"P", EAF:"EAF"}
    if not keep_columns:
        data = data.loc[:,data.columns.isin([CHR,POS,SNP,EA,NEA,BETA,SE,P,EAF])]
    data.rename(columns = rename_dict, inplace=True)
    #Check duplicated column names
    column_counts = Counter(data.columns)
    duplicated_columns = [col for col, count in column_counts.items() if (count > 1) and (col in rename_dict.values())]
    if duplicated_columns:
        raise ValueError(f"After adjusting the column names, the resulting dataframe has duplicated columns. Make sure your dataframe does not have a different column named as {duplicated_columns}.")
    return data

def check_arguments(df, preprocessing, reference_panel, clumped, effect_column, keep_columns, 
                    fill_snpids, fill_coordinates, keep_multi, keep_dups):
    """
    Verify the arguments passed for the GENO initialization and apply logic based on the preprocessing value. See :class:`GENO` for more details.

    Returns:
        tuple: Tuple containing updated values for (keep_columns, keep_multi, keep_dups, fill_snpids, fill_coordinates)

    Raises:
        TypeError: For invalid data types or incompatible argument values.
    """
    
    # Validate df type
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df needs to be a pandas dataframe.")
    
    # Validate preprocessing value
    if preprocessing not in [0,1,2]:
        raise ValueError("preprocessing must be one of [0, 1, 2]. Refer to the GENO class docstring for details.")
    
    # Validate effect_column value
    if not ((effect_column is None) or (effect_column in ["OR", "BETA"])):
        raise ValueError("effect_column must be one of [None, 'OR', 'BETA'].")
    
    # Ensure all other arguments are either None or boolean type
    variables = {
        "clumped": clumped,
        "keep_columns": keep_columns,
        "fill_snpids": fill_snpids,
        "fill_coordinates": fill_coordinates,
        "keep_multi": keep_multi,
        "keep_dups": keep_dups
    }
    for name, value in variables.items():
        if not (value is None or isinstance(value, bool)):
            raise TypeError(f"{name} only accepts values: None, True, or False.")

    # Helper functions for preprocessing logic
    def keeptype_column(arg):
        """Helper function to decide whether to keep columns/multi-values/duplicates."""
        return True if arg is None and preprocessing < 2 else arg
    
    def filltype_column(arg):
        """Helper function to decide whether to fill snpids/coordinates."""
        return False if arg is None and preprocessing == 0 else arg

    # Apply preprocessing logic
    keep_columns = keeptype_column(keep_columns)
    keep_multi = keeptype_column(keep_multi)
    keep_dups = keeptype_column(keep_dups)
    fill_snpids = filltype_column(fill_snpids)
    fill_coordinates = filltype_column(fill_coordinates)

    return keep_columns, keep_multi, keep_dups, fill_snpids, fill_coordinates


def save_data(data, name, path="", fmt="h5", sep="\t", header=True):
    """
    Save a DataFrame to a file in a given format.
    
    Args:
    - data (pd.DataFrame): The data to be saved.
    - name (str): The name of the file without extension.
    - path (str, optional): Directory path for saving. Default is the current directory.
    - fmt (str, optional): Format for the file, e.g., "h5", "csv", "txt", "vcf", "vcf.gz". Default is "h5".
    - sep (str, optional): Delimiter for csv or txt files. Default is tab.
    - header (bool, optional): Whether to include header in csv or txt files. Default is True.
    
    Returns:
    None. But saves the data to a file and prints the file path.
    
    Raises:
    - ValueError: If the provided format is not recognized.
    """
    if path:
        path_name = f"{path}/{name}.{fmt}"
    else:
        path_name = f"{name}.{fmt}"

    if fmt == "h5":
        df = data.copy()
        for col in df.select_dtypes(include='integer').columns:
            df[col] = df[col].astype('float64')
        df.to_hdf(path_name, mode="w", key="data")

    elif fmt in ["csv", "txt"]:
        data.to_csv(path_name, sep=sep, header=header, index=False)

    elif fmt in ["vcf", "vcf.gz"]:
        # to do
        return

    else:
        raise ValueError("The fmt argument takes value in (h5 (default), csv, txt, vcf, vcf.gz).")

    print(f"Data saved to {path_name}")


def Combine_GENO(Gs, name="noname", clumped=False, preprocessing=0):
    """
    Combine a list of GWAS objects into one.

    Args:
    - Gs (list): List of GWAS objects.
    - name (str, optional): Name for the combined object. Default is "noname".
    - clumped (bool, optional): If True, uses the clumped data of each object. Default is False.
    - preprocessing (int, optional): Level of preprocessing to apply. Default is 0.
    
    Returns:
    GENO object: Combined GENO object.
    """
    C = pd.DataFrame()

    if clumped:
        for G in Gs:
            C = pd.concat([C, G.data_clumped])
    else:
        for G in Gs:
            C = pd.concat([C, G.data])

    C = C.reset_index(drop=True)

    return GENO(C, name=name, clumped=clumped, preprocessing=preprocessing)


def delete_tmp():
    """Delete the tmp folder."""
    if os.path.isdir("tmp_GENAL"):
        shutil.rmtree("tmp_GENAL")
        print ("The tmp_GENAL folder has been successfully deleted.")
    else:
        print("There is no tmp_GENAL folder to delete in the current directory.")
    return