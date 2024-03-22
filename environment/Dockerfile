# hash:sha256:e0999f922f4ecd0b5622b0700b6fb0cdc15503e046558fa5db2791d8385d76ea
ARG REGISTRY_HOST
FROM $REGISTRY_HOST/codeocean/ubuntu-desktop-pytorch:2.1.0-cuda11.8-mambaforge23.1.0-4-python3.10.12-fiji20231107-1617-firefox120.0-igv2.16.1-guacamole1.5.2-ubuntu22.04

ARG DEBIAN_FRONTEND=noninteractive

ARG GIT_ASKPASS
ARG GIT_ACCESS_TOKEN
COPY git-askpass /

RUN pip install -U --no-cache-dir \
    gymnasium==0.29.1 \
    h5py==3.10.0 \
    hydra-core==1.3.2 \
    imageio[ffmpeg]==2.34.0 \
    ipykernel==6.29.3 \
    ipywidgets==8.1.2 \
    matplotlib==3.8.3 \
    motornet==0.2.0 \
    omegaconf==2.3.0 \
    python-dotenv==1.0.1 \
    ray[tune]==2.9.3 \
    scikit-learn==1.4.1.post1 \
    wandb==0.16.4

COPY postInstall /
RUN /postInstall
