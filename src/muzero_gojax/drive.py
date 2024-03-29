"""Utility functions for interacting with Google Drive."""

import contextlib
import os
import shutil
import tempfile
from typing import Callable

from absl import flags
from oauth2client.client import GoogleCredentials

from muzero_gojax import logger

_USE_PYDRIVE = flags.DEFINE_bool(
    'use_pydrive', False, 'Whether or not to use PyDrive to save files.')

_GOOGLE_DRIVE = None


def initialize_drive(directory_path: str, flag_values: flags.FlagValues):
    """Initializes the file drive settings.
    
    * May initialize the Google Drive API.
    * Creates the specified directory if it does not exist.
    * Saves the flags in the directory.
    """
    global _GOOGLE_DRIVE  # pylint: disable=global-statement
    if _USE_PYDRIVE.value:
        # pylint: disable=import-outside-toplevel
        from pydrive.auth import GoogleAuth
        from pydrive.drive import GoogleDrive
        gauth = GoogleAuth()
        gauth.credentials = GoogleCredentials.get_application_default()
        _GOOGLE_DRIVE = GoogleDrive(gauth)
        logger.log('Initialized PyDrive.')
    else:
        logger.log('Not using PyDrive.')

    if not directory_exists(directory_path):
        mkdir(directory_path)
        logger.log(f'Created new directory: {directory_path}')

    # Save flags.
    with open_file(os.path.join(directory_path, 'flags.txt'), 'w') as file:
        file.write(flag_values.flags_into_string())


def _get_google_drive_dir(directory_path: str):
    """Gets the Google Drive directory."""
    file_id = 'root'
    for subdir in directory_path.split('/'):
        if not subdir:
            continue
        file_list = _GOOGLE_DRIVE.ListFile({
            'q':
            f"'{file_id}' in parents "
            f"and mimeType='application/vnd.google-apps.folder' "
            f"and title='{subdir}'"
        }).GetList()
        if len(file_list) < 1:
            raise LookupError(f"Failed to find sub directory '{subdir}'")
        if len(file_list) > 1:
            raise LookupError(f"Found multiple sub directories for '{subdir}'")
        file_id = file_list[0]['id']
    return file_list[0]


def _get_google_drive_file(filepath: str):
    """Gets the Google Drive file."""
    head, tail = os.path.split(filepath)
    directory = _get_google_drive_dir(head)
    file_list = _GOOGLE_DRIVE.ListFile({
        'q':
        f"title='{tail}' and '{directory['id']}' in parents "
        f"and mimeType!='application/vnd.google-apps.folder'"
    }).GetList()
    if len(file_list) < 1:
        raise LookupError(
            f"Failed to find file '{tail}' in directory '{head}'")
    if len(file_list) > 1:
        raise LookupError(
            f"Found multiple files '{tail}' in directory '{head}'")
    return file_list[0]


@contextlib.contextmanager
def open_file(filepath: str,
              mode: str | None = None,
              encoding: str | None = None):
    """Opens a file."""
    if _GOOGLE_DRIVE is None:
        try:
            yield open(filepath, mode, encoding=encoding)
        finally:
            pass
    else:
        drive_file = _get_google_drive_file(filepath)
        temp_dir = tempfile.mkdtemp()
        tmpfilepath = os.path.join(temp_dir, drive_file['id'])
        drive_file.GetContentFile(tmpfilepath)
        try:
            yield open(tmpfilepath, mode, encoding=encoding)
        finally:
            shutil.rmtree(temp_dir)


def directory_exists(directory_path: str) -> bool:
    """Checks if a directory exists."""
    if _GOOGLE_DRIVE is None:
        return os.path.isdir(directory_path)
    else:
        try:
            _get_google_drive_dir(directory_path)
            return True
        except LookupError:
            return False


def mkdir(directory_path: str):
    """Creates a directory."""
    if directory_exists(directory_path):
        return
    if _GOOGLE_DRIVE is None:
        os.mkdir(directory_path)
    else:
        head, tail = os.path.split(directory_path)
        parent_dir = _get_google_drive_dir(head)
        folder = _GOOGLE_DRIVE.CreateFile({
            'title':
            tail,
            'parents': [{
                'id': parent_dir['id']
            }],
            'mimeType':
            'application/vnd.google-apps.folder'
        })
        folder.Upload()


def write_file(filepath: str, mode: str, mime_type: str,
               write_fn: Callable[[any], None]):
    """Writes a file."""
    if _GOOGLE_DRIVE is None:
        with open(filepath, mode) as file:
            write_fn(file)
    else:
        try:
            drive_file = _get_google_drive_file(filepath)
        except LookupError:
            head, tail = os.path.split(filepath)
            parent_dir = _get_google_drive_dir(head)
            drive_file = _GOOGLE_DRIVE.CreateFile({
                'title':
                tail,
                'parents': [{
                    'id': parent_dir['id']
                }],
                'mimeType':
                mime_type
            })
        with tempfile.TemporaryDirectory() as tmpdirname:
            tmpfilepath = os.path.join(tmpdirname, drive_file['id'])
            with open(tmpfilepath, mode) as file:
                write_fn(file)
            drive_file.SetContentFile(tmpfilepath)
            drive_file.Upload()
