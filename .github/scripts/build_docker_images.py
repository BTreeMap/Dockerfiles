#!/usr/bin/env python3

import os
import sys
import subprocess
import glob
import multiprocessing
import datetime
import logging

def init_logger():
    logger = logging.getLogger('docker_builder')
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('[%(asctime)s][%(levelname)s] %(message)s', datefmt='%Y-%m-%d.%H-%M-%S')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

def get_env_var(name, default=None):
    value = os.environ.get(name, default)
    if value is None:
        raise ValueError(f"Environment variable '{name}' is not set")
    return value

def build_and_push_image(args):
    (dockerfile_path, base_image, date_str, date_time_str, commit_hash, max_retries, logger_lock) = args
    directory_path = os.path.dirname(dockerfile_path)
    image_name_dir = os.path.basename(directory_path).lower()

    # Construct image tags
    tags = []
    tags.append(f"{base_image}:{image_name_dir}")
    tags.append(f"{base_image}:{image_name_dir}.latest")
    tags.append(f"{base_image}:{image_name_dir}.{date_str}")
    tags.append(f"{base_image}:{image_name_dir}.{date_time_str}")
    tags.append(f"{base_image}:{image_name_dir}.{commit_hash}")
    tags.append(f"{base_image}:{image_name_dir}.{commit_hash}.{date_str}")
    tags.append(f"{base_image}:{image_name_dir}.{commit_hash}.{date_time_str}")

    builder_name = f"builder_{image_name_dir}"

    buildx_command = [
        'docker', 'buildx', 'build',
        '--push', '--no-cache',
        '--builder', builder_name,
        '--platform', 'linux/amd64,linux/arm64'
    ]
    for tag in tags:
        buildx_command.extend(['--tag', tag])
    buildx_command.extend(['--file', dockerfile_path, directory_path])

    # Commands to create and remove builder
    create_builder_command = ['docker', 'buildx', 'create', '--use', '--name', builder_name]
    remove_builder_command = ['docker', 'buildx', 'rm', builder_name]

    # Command to enable binfmt for multi-platform builds
    enable_binfmt_command = ['docker', 'run', '--privileged', '--rm', 'tonistiigi/binfmt', '--install', 'all']

    attempt = 0
    while attempt < max_retries:
        try:
            attempt += 1
            with logger_lock:
                logger.info(f"Building image for {tags[0]} (attempt {attempt}/{max_retries}) with tags:")
                for tag in tags:
                    logger.info(f" - {tag}")
            # Create builder
            subprocess.run(create_builder_command, check=True)
            # Enable binfmt
            subprocess.run(enable_binfmt_command, check=True)
            # Build and push image
            subprocess.run(buildx_command, check=True)
            # Build succeeded, break out of retry loop
            break
        except subprocess.CalledProcessError as e:
            if attempt >= max_retries:
                with logger_lock:
                    logger.error(f"Failed to build image {tags[0]} after {max_retries} attempts")
                raise e
            else:
                with logger_lock:
                    logger.warning(f"Build failed for image {tags[0]} on attempt {attempt}/{max_retries}, retrying...")
        finally:
            # Remove builder
            subprocess.run(remove_builder_command, check=True)
    return

def main():
    logger = init_logger()

    # Get environment variables
    DOCKER_REGISTRY = get_env_var('DOCKER_REGISTRY').lower()
    DOCKER_IMAGE_NAME = get_env_var('DOCKER_IMAGE_NAME').lower()
    MAX_RETRIES = int(os.environ.get('MAX_RETRIES', '3'))
    GITHUB_SHA = get_env_var('GITHUB_SHA')

    # Get current date and time in UTC
    date_time = datetime.datetime.utcnow()
    date_str = date_time.strftime('%Y-%m-%d')
    date_time_str = date_time.strftime('%Y-%m-%d.%H-%M-%S')

    # Base image path
    base_image = f"{DOCKER_REGISTRY}/{DOCKER_IMAGE_NAME}"

    logger.info(f"Starting Docker builds with base image: {base_image}")

    # Find all Dockerfiles
    dockerfiles = glob.glob('**/Dockerfile', recursive=True)
    if not dockerfiles:
        logger.error("No Dockerfiles found.")
        sys.exit(1)

    logger.info("Found Dockerfiles:")
    for dockerfile in dockerfiles:
        logger.info(f" - {dockerfile}")

    # Prepare arguments for building images
    args_list = []
    manager = multiprocessing.Manager()
    logger_lock = manager.Lock()
    for dockerfile in dockerfiles:
        args = (dockerfile, base_image, date_str, date_time_str, GITHUB_SHA, MAX_RETRIES, logger_lock)
        args_list.append(args)

    # Use multiprocessing to build images in parallel
    num_processes = multiprocessing.cpu_count()
    logger.info(f"Starting Docker builds in parallel using up to {num_processes} processes")

    with multiprocessing.Pool(processes=num_processes) as pool:
        pool.map(build_and_push_image, args_list)

if __name__ == '__main__':
    main()
