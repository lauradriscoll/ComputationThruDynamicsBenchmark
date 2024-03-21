# hash:sha256:a5ebd3536e84e780c7335105a19d4e2d9158549ed3001987d0fa5a753bacd2e1
ARG REGISTRY_HOST
FROM $REGISTRY_HOST/codeocean/cuda-miniconda-jupyterlab:latest

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
    opencv-python==4.9.0.80 \
    python-dotenv==1.0.1 \
    pytorch-lightning==2.1.3 \
    ray[tune]==2.9.3 \
    scikit-learn==1.4.1.post1 \
    torch==1.13.1 \
    torchaudio==0.13.1 \
    torchdata==0.5.1 \
    torchmetrics==1.3.0.post0 \
    torchvision==0.14.1 \
    wandb==0.16.4

COPY postInstall /
RUN /postInstall
