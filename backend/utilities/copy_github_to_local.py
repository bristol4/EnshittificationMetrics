#!/usr/bin/env python

"""
Does git status check against GitHub repo for changes / updates.
Does git pull of repo to clone_dir.
Copies clone_dir out to dest_dir_www and dest_dir_back.
Checks for some restarts needed.
ISSUES: Does not clean up or deal with renamed or deleted files!
"""

# Define directories
clone_dir = '/home/bsea/github'
dest_dir_www = '/var/www/em'
dest_dir_back = '/home/bsea/em'

import logging
logging.basicConfig(level = logging.INFO,
                    filename = dest_dir_back + '/utilities/EM_github_pull.log',
                    filemode = 'a',
                    format = '%(asctime)s -%(levelname)s - %(message)s')
import subprocess
import os
import shutil
import re
import time


def check_for_updates(repo_dir):
    try:
        subprocess.run(["git", "fetch"], check=True, cwd=repo_dir)
        result = subprocess.run(["git", "status"], capture_output=True, text=True, cwd=repo_dir)
    except subprocess.CalledProcessError as e:
        logging.error(f'==> Error during git fetch / status: {e}')
        return False
    if "Your branch is behind" in result.stdout:
        logging.info(f'In {repo_dir} git fetch / status indicates updates available.')
        return True
    else:
        logging.info(f'In {repo_dir} git fetch / status indicates up to date.')
        return False


def fetch_and_pull(repo_dir):
    try:
        subprocess.run(["git", "fetch"], check=True, cwd=repo_dir) # don't really need this as just done in check_for_updates, but don't want to rename function and doesn't really hurt
        subprocess.run(["git", "pull"], check=True, cwd=repo_dir)
        logging.info(f'Git fetched and pulled updates in {repo_dir}.')
    except subprocess.CalledProcessError as e:
        logging.error(f'==> Error during fetch or pull: {e}')


requires_a_restart = """
    * Enabling/Disabling Apache Modules, SSL Certificate Changes       --> sudo systemctl restart apache2      (out of scope of this program)
    * Changes to /etc/apache2/sites-available/                         --> sudo systemctl reload apache2       (out of scope of this program)
    * Changes to mod-wsgi configuration                                --> sudo systemctl reload apache2       (in scope)
    * Python Code Changes (Flask app, views, models, app.py, views.py  --> touch /var/www/yourapp/yourapp.wsgi (in scope)
    * Install/update Python packages in virtual environment            --> touch /var/www/yourapp/yourapp.wsgi (in scope)
    * Changes to HTML, CSS, JavaScript, Jinja2 templates, static files --> none                                (in scope)
    * New flask DB schema change file                                  --> flask db upgrade                    (in scope)
    """

def check_for_restart_needed(filename):
    """
    Sets apache_restart_needed flag for change to middleapp.wsgi, or,
    touches middleapp.wsgi for changes to Flask .py or Python packages, or, 
    does a flask db upgrade.
    (May do multiple times per script run.)
    """
    match filename:
        case 'middleapp.wsgi':
            try:
                subprocess.run(['touch', dest_dir_back + '/utilities/apache_reload_needed'], check=True)
                logging.info(f'==> Set apache_reload_needed flag.')
            except subprocess.CalledProcessError as e:
                logging.error(f'==> Unable to set apache_restart_needed flag; got error: {e}')
        case 'EnshittificationMetrics.py' | 'routes.py' | 'models.py' | 'forms.py' | 'Pipfile':
            try:
                subprocess.run(['touch', dest_dir_www + '/middleapp.wsgi'], check=True)
                logging.info(f'==> Touched middleapp.wsgi.')
            except subprocess.CalledProcessError as e:
                logging.error(f'==> Unable to touch middleapp.wsgi; got error: {e}')
        case 'migrations/versions':
            try:
                time.sleep(.25 * 60) # 15 second pause for file copy to settle
                result = subprocess.run(["pipenv", "run", "flask", "db", "upgrade"], cwd=dest_dir_www, check=True)
                logging.info(f'==> Performed flask db upgrade (migrate new schema); output: {result}')
                time.sleep(.25 * 60) # 15 second pause for migration to settle
            except subprocess.CalledProcessError as e:
                logging.error(f'==> Unable to migrate new schema; got error: {e}') # trying to log {result} here might cause another error...


def place_files(src, dest_www, dest_back):
    files_copied = 0
    for dirpath, dirnames, filenames in os.walk(src):
        if 'www' in dirpath:
            dst = dest_www
        elif 'backend' in dirpath:
            dst = dest_back
        else:
            continue # gh (readme, license) or misc git related stuff we don't need to copy
        rough_relative_path = os.path.relpath(dirpath, src)
        # print(f'rough_relative_path = {rough_relative_path}') # for testing
        # split relative path, remove first part (www or backend) and rejoin back together
        path_parts = rough_relative_path.split(os.sep)
        if len(path_parts) > 1:
            relative_path = os.path.join(*path_parts[1:])
        else:
            relative_path = ''
        dst_dirpath = os.path.join(dst, relative_path)
        """ Create directories in the destination if they don't exist """
        if not os.path.exists(dst_dirpath):
            # print(f'Made directory {dst_dirpath}') # for testing
            os.makedirs(dst_dirpath)
            logging.info(f'Made directory {dst_dirpath}') # triggered only rarly when new dir is actually added
        """ Copy files, overwriting if they already exist in the destination """
        for filename in filenames:
            src_file = os.path.join(dirpath, filename)
            dst_file = os.path.join(dst_dirpath, filename)
            if not os.path.exists(dst_file):
                """ copy file if doesn't exist yet """
                try:
                    shutil.copy2(src_file, dst_file)  # copy2 preserves metadata (e.g., timestamps)
                    # print(f'Copied new file {dst_file}') # for testing
                    logging.info(f'Copied new file {dst_file}')
                    files_copied += 1
                except Exception as e:
                    logging.error(f'==> In attempting to copy new file {dst_file}, got error: {e}')
                    continue
                    # print(f'In attempting to copy new file {dst_file}, got error: {e}') # for testing
            else:
                src_mtime = os.path.getmtime(src_file) # Source file modification time
                dst_mtime = os.path.getmtime(dst_file) # Destination file modification time
                if src_mtime > dst_mtime:
                    """ copy file if a newer version """
                    try:
                        subprocess.run(['cp', src_file, dst_file], check=True)
                        # print(f'Copied updated file {dst_file}') # for testing
                        # shutil.copy2, cp -p, and, cp --preserve=timestamps, all fail to overwrite a target not owned by them
                        # shutil.copy2(src_file, dst_file) # copy2 preserves metadata (e.g., timestamps)
                        logging.info(f'Copied updated file {dst_file}')
                        files_copied += 1
                    except subprocess.CalledProcessError as e:
                        logging.error(f'==> In attempting to copy updated file {dst_file}, got error: {e}')
                        # print(f'In attempting to copy updated file {dst_file}, got error: {e}') # for testing
                        continue
                else:
                    """ skip file, no copy, if file same or older """
                    # logging.warning(f'No copy performed - {dst_file} file exists and is same (or older) timestamp.') # not really a warning  - expected / desired behavior
                    continue
            """ if just copied a Pipfile, then run "pipenv install" where the Pipfile is """
            if filename == 'Pipfile':
                try:
                    subprocess.run(['pipenv', 'install'], cwd=dst_dirpath, check=True)
                    logging.info(f'==> pipenv install run - at location {dst_dirpath}')
                except Exception as e:
                    logging.error(f'==> On running pipenv install at location {dst_dirpath} get error: {e}')
            """ check if copying this new file requires some restart; special case for schema change file """
            pattern = r".*/migrations/versions*"
            if re.search(pattern, dirpath):
                check_for_restart_needed('migrations/versions')
            else:
                check_for_restart_needed(filename)
    logging.info(f'Copied {files_copied} files.')


def main():
    logging.info(f'Starting GitHub {clone_dir} sync and file copy to {dest_dir_www} and {dest_dir_back}.')
    if check_for_updates(clone_dir):
        fetch_and_pull(clone_dir)
        place_files(clone_dir, dest_dir_www, dest_dir_back)
    logging.info(f'Completed.\n')


if __name__ == '__main__':
    main()
