# derived from lfads-torch (arsedler9)
import io

import matplotlib.pyplot as plt
import numpy as np
import pytorch_lightning as pl
import torch
import wandb
from sklearn.decomposition import PCA

from ctd.data_modeling.callbacks.metrics import (
    linear_regression,
    r2_score,
    regression_r2_score,
)


def get_tensorboard_summary_writer(writers):
    """Gets the TensorBoard SummaryWriter from a logger
    or logger collection to allow writing of images.
    Parameters
    ----------
    writers : obj or list[obj]
        An object or list of objects to search for the
        SummaryWriter.
    Returns
    -------
    torch.utils.tensorboard.writer.SummaryWriter
        The SummaryWriter object.
    """
    writer_list = writers if isinstance(writers, list) else [writers]
    for writer in writer_list:
        if isinstance(writer, torch.utils.tensorboard.writer.SummaryWriter):
            return writer
    else:
        return None


def fig_to_rgb_array(fig):
    """Converts a matplotlib figure into an array
    that can be logged to tensorboard.
    Parameters
    ----------
    fig : matplotlib.figure.Figure
        The figure to be converted.
    Returns
    -------
    np.array
        The figure as an HxWxC array of pixel values.
    """
    # Convert the figure to a numpy array
    with io.BytesIO() as buff:
        fig.savefig(buff, format="raw")
        buff.seek(0)
        fig_data = np.frombuffer(buff.getvalue(), dtype=np.uint8)
    w, h = fig.canvas.get_width_height()
    im = fig_data.reshape((int(h), int(w), -1))
    plt.close()
    return im


class TrajectoryPlotModels(pl.Callback):
    """Plots the top-3 PC's of the latent trajectory for
    all samples in the validation set and logs to tensorboard.
    """

    def __init__(self, log_every_n_epochs=100):
        """Initializes the callback.
        Parameters
        ----------
        log_every_n_epochs : int, optional
            The frequency with which to plot and log, by default 100
        """
        self.log_every_n_epochs = log_every_n_epochs

    def on_validation_epoch_end(self, trainer, pl_module):
        """Logs plots at the end of the validation epoch.
        Parameters
        ----------
        trainer : pytorch_lightning.Trainer
            The trainer currently handling the model.
        pl_module : pytorch_lightning.LightningModule
            The model currently being trained.
        """
        # Skip evaluation for most epochs to save time
        if (trainer.current_epoch % self.log_every_n_epochs) != 0:
            return

        # Check for the TensorBoard SummaryWriter
        logger = trainer.loggers[2]
        writer = trainer.loggers[0]
        if writer is None:
            return
        # Get the validation dataset
        val_dataloader = trainer.datamodule.val_dataloader()

        # Pass data through the model
        latents1 = [batch[0] for batch in val_dataloader]
        latents1 = torch.cat(latents1).detach().cpu().numpy()

        latents2 = [batch[1] for batch in val_dataloader]
        latents2 = torch.cat(latents2).detach().cpu().numpy()

        # Reduce dimensionality if necessary
        n_samp, n_step, n_lats = latents1.shape
        if n_lats > 3:
            latents_flat = latents1.reshape(-1, n_lats)
            pca = PCA(n_components=3)
            latents1 = pca.fit_transform(latents_flat)
            latents1 = latents1.reshape(n_samp, n_step, 3)
            explained_variance = np.sum(pca.explained_variance_ratio_)
        else:
            explained_variance = 1.0

        n_samp, n_step, n_lats = latents2.shape
        if n_lats > 3:
            latents_flat = latents2.reshape(-1, n_lats)
            pca = PCA(n_components=3)
            latents2 = pca.fit_transform(latents_flat)
            latents2 = latents2.reshape(n_samp, n_step, 3)
            explained_variance = np.sum(pca.explained_variance_ratio_)
        else:
            explained_variance = 1.0

        # Create figure and plot trajectories
        fig = plt.figure(figsize=(10, 10))
        ax = fig.add_subplot(121, projection="3d")
        for traj in latents1:
            ax.plot(*traj.T, alpha=0.2, linewidth=0.5)
        ax.scatter(*latents1[:, 0, :].T, alpha=0.1, s=10, c="g")
        ax.scatter(*latents1[:, -1, :].T, alpha=0.1, s=10, c="r")
        ax.set_title(f"explained variance: {explained_variance:.2f}")

        ax = fig.add_subplot(122, projection="3d")
        for traj in latents2:
            ax.plot(*traj.T, alpha=0.2, linewidth=0.5)
        ax.scatter(*latents2[:, 0, :].T, alpha=0.1, s=10, c="g")
        ax.scatter(*latents2[:, -1, :].T, alpha=0.1, s=10, c="r")
        ax.set_title(f"explained variance: {explained_variance:.2f}")

        plt.tight_layout()
        # Log the plot to tensorboard

        logger.log(
            {
                "trajectory_plot_models": wandb.Image(fig),
                "global_step": trainer.global_step,
            }
        )


class RasterPlot(pl.Callback):
    """Plots validation spiking data side-by-side with
    inferred inputs and rates and logs to tensorboard.
    """

    def __init__(self, n_samples=2, log_every_n_epochs=20):
        """Initializes the callback.
        Parameters
        ----------
        n_samples : int, optional
            The number of samples to plot, by default 2
        log_every_n_epochs : int, optional
            The frequency with which to plot and log, by default 20
        """
        self.n_samples = n_samples
        self.log_every_n_epochs = log_every_n_epochs

    def on_validation_epoch_end(self, trainer, pl_module):
        """Logs plots at the end of the validation epoch.
        Parameters
        ----------
        trainer : pytorch_lightning.Trainer
            The trainer currently handling the model.
        pl_module : pytorch_lightning.LightningModule
            The model currently being trained.
        """
        if (trainer.current_epoch % self.log_every_n_epochs) != 0:
            return

        # Check for the TensorBoard SummaryWriter
        logger = trainer.loggers[2]
        writer = trainer.loggers[0]
        if writer is None:
            return
        # Get data samples
        dataloader = trainer.datamodule.val_dataloader()
        spikes, _, inputs, extras, latents, inds, rates = next(iter(dataloader))
        spikes = spikes.to(pl_module.device)
        inputs = inputs.to(pl_module.device)
        # Compute model output
        pred_logrates, pred_latents = pl_module(spikes, inputs)
        # Convert everything to numpy
        spikes = spikes.detach().cpu().numpy()
        rates = rates.detach().cpu().numpy()
        if hasattr(pl_module, "intensity_func"):
            pred_rates = pl_module.intensity_func(pred_logrates).detach().cpu().numpy()
        else:
            pred_rates = torch.exp(pred_logrates).detach().cpu().numpy()
        # Create subplots
        pred_rates_flat = torch.Tensor(pred_rates.reshape(-1, pred_rates.shape[-1]))
        rates_flat = torch.Tensor(rates.reshape(-1, rates.shape[-1]))
        r2 = r2_score(pred_rates_flat, rates_flat)
        plot_arrays = [spikes, rates, pred_rates]
        fig, axes = plt.subplots(
            len(plot_arrays),
            self.n_samples,
            sharex=True,
            sharey="row",
            figsize=(3 * self.n_samples, 10),
        )
        for i, ax_col in enumerate(axes.T):
            for ax, array in zip(ax_col, plot_arrays):
                ax.imshow(array[i].T, interpolation="none", aspect="auto")
        fig.suptitle(f"Rate R2: {r2:.2f}")
        plt.tight_layout()

        # Log the plot to tensorboard
        logger.log_image(key="raster_plot", images=[wandb.Image(fig)])


class TrajectoryPlot(pl.Callback):
    """Plots the top-3 PC's of the latent trajectory for
    all samples in the validation set and logs to tensorboard.
    """

    def __init__(self, log_every_n_epochs=100):
        """Initializes the callback.
        Parameters
        ----------
        log_every_n_epochs : int, optional
            The frequency with which to plot and log, by default 100
        """
        self.log_every_n_epochs = log_every_n_epochs

    def on_validation_epoch_end(self, trainer, pl_module):
        """Logs plots at the end of the validation epoch.
        Parameters
        ----------
        trainer : pytorch_lightning.Trainer
            The trainer currently handling the model.
        pl_module : pytorch_lightning.LightningModule
            The model currently being trained.
        """
        # Skip evaluation for most epochs to save time
        if (trainer.current_epoch % self.log_every_n_epochs) != 0:
            return

        # Check for the TensorBoard SummaryWriter
        logger = trainer.loggers[2]
        # Get the validation dataset
        val_dataloader = trainer.datamodule.val_dataloader()

        # Pass data through the model
        def batch_fwd(pl_module, batch):
            return pl_module(
                batch[0].to(pl_module.device), batch[2].to(pl_module.device)
            )

        latents = [batch_fwd(pl_module, batch)[1] for batch in val_dataloader]
        latents = torch.cat(latents).detach().cpu().numpy()
        # Reduce dimensionality if necessary
        n_samp, n_step, n_lats = latents.shape
        if n_lats > 3:
            latents_flat = latents.reshape(-1, n_lats)
            pca = PCA(n_components=3)
            latents = pca.fit_transform(latents_flat)
            latents = latents.reshape(n_samp, n_step, 3)
            explained_variance = np.sum(pca.explained_variance_ratio_)
        else:
            explained_variance = 1.0
        # Create figure and plot trajectories
        fig = plt.figure(figsize=(10, 10))
        ax = fig.add_subplot(111, projection="3d")
        for traj in latents:
            ax.plot(*traj.T, alpha=0.2, linewidth=0.5)
        ax.scatter(*latents[:, 0, :].T, alpha=0.1, s=10, c="g")
        ax.scatter(*latents[:, -1, :].T, alpha=0.1, s=10, c="r")
        ax.set_title(f"explained variance: {explained_variance:.2f}")
        plt.tight_layout()
        # Log the plot to tensorboard
        logger.log_image(key="trajectory_plot", images=[wandb.Image(fig)])


class LatentRegressionPlot(pl.Callback):
    def __init__(self, n_dims=10, log_every_n_epochs=100):
        self.n_dims = n_dims
        self.log_every_n_epochs = log_every_n_epochs

    def on_validation_epoch_end(self, trainer, pl_module):
        """Logs plots at the end of the validation epoch.
        Parameters
        ----------
        trainer : pytorch_lightning.Trainer
            The trainer currently handling the model.
        pl_module : pytorch_lightning.LightningModule
            The model currently being trained.
        """
        # Skip evaluation for most epochs to save time
        if (trainer.current_epoch % self.log_every_n_epochs) != 0:
            return
        # Check for the TensorBoard SummaryWriter
        logger = trainer.loggers[2]
        # Get the validation dataset
        val_dataloader = trainer.datamodule.val_dataloader()

        # Pass data through the model
        def batch_fwd(pl_module, batch):
            return pl_module(
                batch[0].to(pl_module.device), batch[2].to(pl_module.device)
            )

        true_latents = torch.cat([batch[4] for batch in val_dataloader])
        true_latents = true_latents.to(pl_module.device)
        pred_latents = [batch_fwd(pl_module, batch)[1] for batch in val_dataloader]
        pred_latents = torch.cat(pred_latents)
        B, T, N = true_latents.shape
        true_latents = true_latents.reshape(B * T, N)
        regr_latents = linear_regression(true_latents, pred_latents)
        # Convert latents to numpy
        pred_latents = pred_latents.detach().cpu().numpy()
        regr_latents = regr_latents.detach().cpu().numpy()

        # Reduce dimensionality if necessary
        n_samp, n_step, n_lats = pred_latents.shape
        if n_lats > 3:
            pred_latents_flat = pred_latents.reshape(-1, n_lats)
            regr_latents_flat = regr_latents.reshape(-1, n_lats)
            pca = PCA(n_components=3)
            pred_latents = pca.fit_transform(pred_latents_flat)
            explained_variance = np.sum(pca.explained_variance_ratio_)
            regr_latents = pca.transform(regr_latents_flat)
            pred_latents = pred_latents.reshape(n_samp, n_step, 3)
            regr_latents = regr_latents.reshape(n_samp, n_step, 3)
        else:
            regr_latents = regr_latents.reshape(n_samp, n_step, -1)
            explained_variance = 1.0
        # Create figure and plot trajectories
        fig = plt.figure(figsize=(10, 10))
        ax = fig.add_subplot(111, projection="3d")
        for color, latents in zip([None, "b"], [pred_latents, regr_latents]):
            for traj in latents:
                ax.plot(*traj.T, color=color, alpha=0.2, linewidth=0.5)
            ax.scatter(*latents[:, 0, :].T, alpha=0.1, s=10, c="g")
            ax.scatter(*latents[:, -1, :].T, alpha=0.1, s=10, c="r")
        ax.set_title(f"explained variance: {explained_variance:.2f}")
        plt.tight_layout()
        # Log the plot to tensorboard
        logger.log_image(key="latent_reg_plot", images=[wandb.Image(fig)])


class WarpingVisualizationPlot(pl.Callback):
    def __init__(self, log_every_n_epochs=100):
        self.log_every_n_epochs = log_every_n_epochs

    def on_sanity_check_end(self, trainer, pl_module):
        """Logs plots at the end of the validation epoch.
        Parameters
        ----------
        trainer : pytorch_lightning.Trainer
            The trainer currently handling the model.
        pl_module : pytorch_lightning.LightningModule
            The model currently being trained.
        """
        # Skip evaluation for most epochs to save time
        if (trainer.current_epoch % self.log_every_n_epochs) != 0:
            return

        num_to_plot = np.arange(trainer.datamodule.obs_dim)
        # Check for the TensorBoard SummaryWriter
        logger = trainer.loggers[2]
        # Get the validation dataset
        val_dataloader = trainer.datamodule.val_dataloader()
        readout = trainer.datamodule.readout

        true_rates = torch.cat([batch[6] for batch in val_dataloader])
        true_rates = true_rates.detach().cpu().numpy()

        true_latents = torch.cat([batch[4] for batch in val_dataloader])
        true_latents = true_latents.detach().cpu().numpy()
        num_neurons = true_rates.shape[2]
        num_latents = true_latents.shape[2]
        fig1, ax1 = plt.subplots()
        flatRates1 = np.reshape(true_rates, (-1, num_neurons))
        flatLatents1 = np.reshape(true_latents, (-1, num_latents))

        activity = flatLatents1 @ readout

        orig_mean = np.mean(activity, axis=0, keepdims=True)
        orig_std = np.std(activity, axis=0, keepdims=True)
        activity = (activity - orig_mean) / orig_std

        predRatesCut1 = activity[0:-1:50, :]
        flatRatesCut1 = flatRates1[0:-1:50, :]

        for i in num_to_plot:
            ax1.scatter(predRatesCut1[:, i], flatRatesCut1[:, i], s=0.5)

        fig, ax = plt.subplots(len(num_to_plot), 1)
        plt.xlim((-1.5, 1.5))
        count = 0
        for i in num_to_plot:

            flat_rates = np.reshape(true_rates[:, :, i], (-1, 1))
            flat_latents = np.reshape(true_latents, (-1, num_latents))

            pred_rates = flat_latents[0:-1:50, :] @ readout
            flat_rates_cut = flat_rates[0:-1:50, :]
            ax[count].scatter(pred_rates[:, i], flat_rates_cut)
            count += 1

        plt.tight_layout()
        # Log the plot to tensorboard
        logger.log(key="warp_viz", images=[wandb.Image(fig1)])


class TrajectoryPlotOverTimeCallback(pl.Callback):
    def __init__(self, log_every_n_epochs=100, num_trials_to_plot=5, axis_num=0):

        self.log_every_n_epochs = log_every_n_epochs
        self.num_trials_to_plot = num_trials_to_plot
        self.axis_num = axis_num

    def on_validation_epoch_end(self, trainer, pl_module):

        # Skip evaluation for most epochs to save time
        if (trainer.current_epoch % self.log_every_n_epochs) != 0:
            return
        # Check for the TensorBoard SummaryWriter
        logger = trainer.loggers[2]
        # Get trajectories and model predictions
        dataloader = trainer.datamodule.val_dataloader()

        spikes, _, inputs, extra, latents, _, rates = next(iter(dataloader))
        spikes = spikes.to(pl_module.device)
        inputs = inputs.to(pl_module.device)
        # Compute model output
        trajs_out, _ = pl_module(spikes, inputs)
        # Plot the true and predicted trajectories
        trial_vec = torch.tensor(
            np.random.randint(0, trajs_out.shape[0], self.num_trials_to_plot)
        )
        fig, ax = plt.subplots()
        traj_in = rates
        t1 = np.linspace(0, 1, len(trial_vec) * trajs_out.shape[1])

        def prep_trajs(x):
            return x[trial_vec, :, self.axis_num].detach().cpu().numpy().flatten()

        trajs_out = prep_trajs(trajs_out)

        if hasattr(pl_module, "intensity_func"):
            trajs_out = pl_module.intensity_func(trajs_out)
            if (
                hasattr(pl_module, "intensity_func_name")
                and pl_module.intensity_func_name == "passthrough"
            ):
                trajs_out = trajs_out**2
        else:
            trajs_out = np.exp(trajs_out)
        ax.plot(t1, prep_trajs(traj_in), "C0", label="Actual rates")
        ax.plot(t1, trajs_out, "C1", label="Pred rates")
        ax.set_xlabel("Time (AU)")
        ax.set_ylabel("Firing (AU)")
        ax.set_title(f"axis {self.axis_num}, {self.num_trials_to_plot} trials")
        ax.legend()
        # Log the plot to tensorboard
        logger.log_image(key="traj_plot_over_time", images=[wandb.Image(fig)])


class AvgFiringRateCallback(pl.Callback):
    def __init__(self, log_every_n_epochs=100):
        self.log_every_n_epochs = log_every_n_epochs

    def on_validation_epoch_end(self, trainer, pl_module):
        # Skip evaluation for most epochs to save time
        if (trainer.current_epoch % self.log_every_n_epochs) != 0:
            return
        # Check for the TensorBoard SummaryWriter
        logger = trainer.loggers[2]
        # Get trajectories and model predictions
        dataloader = trainer.datamodule.val_dataloader()

        _, _, inputs, extra, latents, _, rates = next(iter(dataloader))
        # Plot the true and predicted trajectories
        fig, ax = plt.subplots()

        x = rates.detach().cpu().numpy()
        meanRates = np.mean(x, 0)
        meanRates = np.mean(meanRates, 0)
        t = range(len(meanRates))

        ax.bar(t, meanRates)
        ax.set_xlabel("Unit Num")
        ax.set_ylabel("Firing (AU)")
        ax.set_title("Average rate of each simulated neuron")
        # Log the plot to tensorboard
        logger.log_image(key="avg_dim_rate", images=[wandb.Image(fig)])


class RateLatentMatchCallback(pl.Callback):
    def __init__(self, log_every_n_epochs=20):

        self.log_every_n_epochs = log_every_n_epochs

    def on_validation_epoch_end(self, trainer, pl_module):

        # Skip evaluation for most epochs to save time
        if (trainer.current_epoch % self.log_every_n_epochs) != 0:
            return
        # Check for the TensorBoard SummaryWriter
        logger = trainer.loggers[2]
        # Get trajectories and model predictions
        dataloader = trainer.datamodule.val_dataloader()

        def batch_fwd(pl_module, batch):
            return pl_module(
                batch[0].to(pl_module.device), batch[2].to(pl_module.device)
            )

        # if pl_module has a variable called "intensity_func"
        # then use it to compute the rates

        rates = torch.exp(
            torch.cat([batch_fwd(pl_module, batch)[0] for batch in dataloader])
        )
        e_rates, m_rates, l_rates = torch.chunk(rates, 3, dim=1)
        latents = torch.cat([batch_fwd(pl_module, batch)[1] for batch in dataloader])
        e_latents, m_latents, l_latents = torch.chunk(latents, 3, dim=1)
        true_latents = torch.cat([batch[4] for batch in dataloader]).to(
            pl_module.device
        )
        e_true_latents, m_true_latents, l_true_latents = torch.chunk(
            true_latents, 3, dim=1
        )
        true_rates = torch.cat([batch[5] for batch in dataloader]).to(pl_module.device)
        e_true_rates, m_true_rates, l_true_rates = torch.chunk(true_rates, 3, dim=1)
        # Fit CCA on the true and predicted latents

        results = {
            "valid/r2_observ": r2_score(rates, true_rates),
            "valid/r2_observ/early": r2_score(e_rates, e_true_rates),
            "valid/r2_observ/middle": r2_score(m_rates, m_true_rates),
            "valid/r2_observ/late": r2_score(l_rates, l_true_rates),
            "valid/r2_latent": regression_r2_score(true_latents, latents),
            "valid/r2_latent/early": regression_r2_score(e_true_latents, e_latents),
            "valid/r2_latent/middle": regression_r2_score(m_true_latents, m_latents),
            "valid/r2_latent/late": regression_r2_score(l_true_latents, l_latents),
            "global_step": trainer.global_step,
        }
        self.log_dict(results)
        logger.log_metrics(results)


class InputsPlot(pl.Callback):
    """Plots the inputs fed to the DMFC model. Makes sure that it's doing what
    I want it to do.
    """

    def __init__(
        self,
        n_samples=2,
        log_every_n_epochs=20,
    ):
        self.n_samples = n_samples
        self.log_every_n_epochs = log_every_n_epochs

    def on_validation_epoch_end(self, trainer, pl_module):
        """Logs plots at the end of the validation epoch.

        Parameters
        ----------
        trainer : pytorch_lightning.Trainer
            The trainer currently handling the model.
        pl_module : pytorch_lightning.LightningModule
            The model currently being trained.
        """
        if (trainer.current_epoch % self.log_every_n_epochs) != 0:
            return

        # Check for the TensorBoard SummaryWriter
        logger = trainer.loggers[2]

        # Get inputs for plotting
        dataloader = trainer.datamodule.val_dataloader()
        batch = next(iter(dataloader))
        spikes, _, inputs, extra, latents, _, rates = batch
        n_inputs = inputs.shape[2]

        # Create subplots
        fig, axes = plt.subplots(
            self.n_samples,
            1,
            sharex=True,
            sharey=False,
            figsize=(10, 10),
        )
        for i, ax_row in enumerate(axes):
            for j in range(n_inputs):
                ax_row.plot(inputs[i, :, j])
                ax_row.set_xlabel("time (bins)")
                ax_row.set_ylabel("Input signal (AU)")
        plt.tight_layout()

        # Log the plot to tensorboard
        logger.log_image(key="inputs_to_network", images=[wandb.Image(fig)])
