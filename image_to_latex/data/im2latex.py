import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

from image_to_latex.data.base_data_module import BaseDataModule
from image_to_latex.data.base_dataset import BaseDataset
from image_to_latex.data.same_size_batch_sampler import SameSizeBatchSampler
from image_to_latex.utils.data import Tokenizer
from image_to_latex.utils.misc import (
    download_url,
    extract_tar_file,
    find_max_length,
)


DATA_DIRNAME = BaseDataModule.data_dirname()
FORMULA_FILENAME = DATA_DIRNAME / "im2latex_formulas.norm.lst"
VOCAB_FILENAME = DATA_DIRNAME / "vocab.json"


class Im2Latex(BaseDataModule):
    """Data processing for the Im2Latex-100K dataset.

    Attributes:
        batch_size: The number of samples per batch.
        num_workers: The number of subprocesses to use for data loading.
        tokenizer: A tokenizer object.
        image_height: Height of resized image.
        image_width: Width of resized image.
        train_dataset: Train dataset.
        val_dataset: Validation dataset.
        test_dataset: Test dataset.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tokenizer = Tokenizer()

    def config(self) -> Dict[str, Any]:
        """Returns important configuration for reproducibility."""
        return {
            "batch_size": self.batch_size,
            "num_workers": self.num_workers,
        }

    def prepare_data(self) -> None:
        """Download the dataset and save to disk."""
        DATA_DIRNAME.mkdir(parents=True, exist_ok=True)
        cur_dir = os.getcwd()
        os.chdir(DATA_DIRNAME)
        with open(DATA_DIRNAME / "metadata.json") as f:
            metadata = json.load(f)
        for entry in metadata:
            filename = entry["filename"]
            url = entry["url"]
            # No need to download the file if it already exists in the data
            # directory
            if Path(filename).is_file():
                continue
            download_url(url, filename)
            if filename == "formula_images_processed.tar.gz":
                extract_tar_file(filename)
        os.chdir(cur_dir)

    def create_datasets(self) -> None:
        """Load images and formulas, and assign them to a `torch Dataset`.

        `self.train_dataset`, `self.val_dataset` and `self.test_dataset` will
        be assigned after this method is called.
        """

        def _create_dataset(
            img_names: Iterable[str],
            formulas: Iterable[Iterable[str]],
            max_seq_len: int,
        ) -> Dataset:
            images = []
            for img_name in img_names:
                image = Image.open(_img_filename(img_name)).convert("L")
                images.append(image)
            targets = self.tokenizer.index(
                formulas, add_sos=True, add_eos=True, pad_to=max_seq_len
            )
            return BaseDataset(
                images, torch.LongTensor(targets), self.transform
            )

        print("Loading datasets...")

        formulas = get_formulas()

        train_img_names, train_formula_indices = load_split_file("train")
        val_img_names, val_formula_indices = load_split_file("val")
        test_img_names, test_formula_indices = load_split_file("test")

        train_formulas = filter_formulas(formulas, train_formula_indices)
        val_formulas = filter_formulas(formulas, val_formula_indices)
        test_formulas = filter_formulas(formulas, test_formula_indices)

        # For train and validation datasets
        max_seq_len = max(
            find_max_length(train_formulas), find_max_length(val_formulas)
        )
        max_seq_len += 2  # Add two for start token and end token
        self.tokenizer.build(train_formulas)
        self.train_dataset = _create_dataset(
            train_img_names, train_formulas, max_seq_len
        )
        self.val_dataset = _create_dataset(
            val_img_names, val_formulas, max_seq_len
        )

        # For test dataset
        max_seq_len = find_max_length(test_formulas)
        max_seq_len += 2  # Add two for start token and end token
        # Filter out formulas that have zero length
        test_img_names_ = []
        test_formulas_ = []
        for img_name, formula in zip(test_img_names, test_formulas):
            if len(formula) > 0:
                test_img_names_.append(img_name)
                test_formulas_.append(formula)
        self.test_dataset = _create_dataset(
            test_img_names_, test_formulas_, max_seq_len
        )

    def get_dataloader(self, split: str) -> Optional[DataLoader]:
        """Returns a `torch Dataloader` object."""
        assert split in ["train", "val", "test"]
        print(f"Preparing {split}_dataloader...")
        dataset = getattr(self, f"{split}_dataset")
        batch_sampler = SameSizeBatchSampler(
            dataset, batch_size=self.batch_size, shuffle=(split == "train")
        )
        dataloader = DataLoader(
            dataset,
            batch_sampler=batch_sampler,
            num_workers=self.num_workers,
            pin_memory=torch.cuda.is_available(),
        )
        return dataloader


def get_formulas() -> List[List[str]]:
    """Returns all the formulas in the formula file."""
    with open(FORMULA_FILENAME) as f:
        formulas = [formula.strip("\n").split() for formula in f.readlines()]
    return formulas


def load_split_file(split: str) -> Tuple[List[str], List[int]]:
    """Load image names and formula indices from a split file."""
    img_names = []
    formula_indices = []
    with open(_split_filename(split)) as f:
        for line in f:
            img_name, formula_idx = line.strip("\n").split()
            img_names.append(img_name)
            formula_indices.append(int(formula_idx))
    return img_names, formula_indices


def filter_formulas(
    formulas: List[List[str]], formula_indices: List[int]
) -> List[List[str]]:
    """Filter formulas by indices."""
    return [formulas[idx] for idx in formula_indices]


def _split_filename(split: str) -> Path:
    """Returns the path to a split file."""
    if split == "val":
        split = "validate"
    return DATA_DIRNAME / f"im2latex_{split}_filter.lst"


def _img_filename(img_name: str) -> Path:
    """Returns the path to an image."""
    return DATA_DIRNAME / "formula_images_processed" / img_name
