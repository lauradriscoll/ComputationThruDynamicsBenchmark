import pytorch_lightning as pl
import torch
from torch import nn

from ctd.data_modeling.datamodules.LFADS.tuples import SessionBatch, SessionOutput
from ctd.data_modeling.extensions.LFADS.metrics import ExpSmoothedMetric, r2_score

from .modules import augmentations
from .modules.decoder import Decoder
from .modules.encoder import Encoder
from .modules.l2 import compute_l2_penalty
from .modules.priors import Null


class LFADS(pl.LightningModule):
    def __init__(
        self,
        gen_type: str,
        inv_encoder: bool,
        encod_data_dim: int,
        encod_seq_len: int,
        recon_seq_len: int,
        ext_input_dim: int,
        ic_enc_seq_len: int,
        ic_enc_dim: int,
        ci_enc_dim: int,
        ci_lag: int,
        con_dim: int,
        co_dim: int,
        ic_dim: int,
        gen_dim: int,
        fac_dim: int,
        dropout_rate: float,
        reconstruction: nn.ModuleList,
        variational: bool,
        co_prior: nn.Module,
        ic_prior: nn.Module,
        ic_post_var_min: float,
        cell_clip: float,
        train_aug_stack: augmentations.AugmentationStack,
        infer_aug_stack: augmentations.AugmentationStack,
        readin: nn.ModuleList,
        readout: nn.ModuleList,
        loss_scale: float,
        recon_reduce_mean: bool,
        lr_scheduler: bool,
        lr_init: float,
        lr_stop: float,
        lr_decay: float,
        lr_patience: int,
        lr_adam_beta1: float,
        lr_adam_beta2: float,
        lr_adam_epsilon: float,
        weight_decay: float,
        l2_start_epoch: int,
        l2_increase_epoch: int,
        l2_ic_enc_scale: float,
        l2_ci_enc_scale: float,
        l2_gen_scale: float,
        l2_con_scale: float,
        l2_readout_scale: float,
        kl_start_epoch: int,
        kl_increase_epoch: int,
        kl_ic_scale: float,
        kl_co_scale: float,
    ):
        super().__init__()
        self.save_hyperparameters(
            ignore=["ic_prior", "co_prior", "reconstruction", "readin", "readout"],
        )
        self.gen_type = gen_type
        self.inv_encoder = inv_encoder
        # Store `co_prior` on `hparams` so it can be accessed in decoder
        self.hparams.co_prior = co_prior
        # Make sure the nn.ModuleList arguments are all the same length
        assert len(readin) == len(readout) == len(reconstruction)
        # Make sure that non-variational models use null priors
        if not variational:
            assert isinstance(ic_prior, Null) and isinstance(co_prior, Null)

        # Store the readin network
        self.readin = readin[0]
        # Decide whether to use the controller
        self.use_con = all([ci_enc_dim > 0, con_dim > 0, co_dim > 0])
        # Create the encoder and decoder
        self.encoder = Encoder(hparams=self.hparams)
        self.decoder = Decoder(hparams=self.hparams)
        # Store the readout network
        self.readout = readout[0]
        # Create object to manage reconstruction
        self.recon = reconstruction[0]
        # Store the trainable priors
        self.ic_prior = ic_prior
        self.co_prior = co_prior
        # Create metric for exponentially-smoothed `valid/recon`
        self.valid_recon_smth = ExpSmoothedMetric(coef=0.3)
        # Store the data augmentation stacks
        self.train_aug_stack = train_aug_stack
        self.infer_aug_stack = infer_aug_stack

    def forward(
        self,
        batch: dict[SessionBatch],
        sample_posteriors: bool = False,
        output_means: bool = True,
    ) -> dict[SessionOutput]:
        # Allow SessionBatch input
        # Pass the data through the readin networks
        encod_data = self.readin(batch.encod_data)
        # Collect the external inputs
        ext_input = batch.ext_input
        # Pass the data through the encoders
        # import pdb; pdb.set_trace()
        ic_mean, ic_std, ci = self.encoder(encod_data)
        # Create the posterior distribution over initial conditions
        ic_post = self.ic_prior.make_posterior(ic_mean, ic_std)
        # Choose to take a sample or to pass the mean
        ic_samp = ic_post.rsample() if sample_posteriors else ic_mean
        if self.inv_encoder:
            ic_samp = self.readout(ic_samp, reverse=True)

        # Unroll the decoder to estimate latent states
        (
            gen_init,
            gen_states,
            con_states,
            co_means,
            co_stds,
            gen_inputs,
            factors,
        ) = self.decoder(ic_samp, ci, ext_input, sample_posteriors=sample_posteriors)
        # Convert the factors representation into output distribution parameters
        output_params = self.readout(factors)
        # Separate parameters of the output distribution
        output_params = self.recon.reshape_output_params(output_params)
        # Convert the output parameters to means if requested
        if output_means:
            output_params = self.recon.compute_means(output_params)
        # Separate model outputs by session
        output = [
            output_params,
            factors,
            ic_mean,
            ic_std,
            co_means,
            co_stds,
            gen_states,
            gen_init,
            gen_inputs,
            con_states,
        ]
        # Return the parameter estimates and all intermediate activations
        return SessionOutput(*output)

    def configure_optimizers(self):
        hps = self.hparams
        # Create an optimizer
        optimizer = torch.optim.AdamW(
            self.parameters(),
            lr=hps.lr_init,
            betas=(hps.lr_adam_beta1, hps.lr_adam_beta2),
            eps=hps.lr_adam_epsilon,
            weight_decay=hps.weight_decay,
        )
        if hps.lr_scheduler:
            # Create a scheduler to reduce the learning rate over time
            scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
                optimizer=optimizer,
                mode="min",
                factor=hps.lr_decay,
                patience=hps.lr_patience,
                threshold=0.0,
                min_lr=hps.lr_stop,
                verbose=True,
            )
            return {
                "optimizer": optimizer,
                "lr_scheduler": scheduler,
                "monitor": "valid/recon_smth",
            }
        else:
            return optimizer

    def _compute_ramp(self, start, increase):
        # Compute a coefficient that ramps from 0 to 1 over `increase` epochs
        ramp = (self.current_epoch + 1 - start) / (increase + 1)
        return torch.clamp(torch.tensor(ramp), 0, 1)

    def on_before_optimizer_step(self, optimizer):  # , optimizer_idx):
        hps = self.hparams
        # Gradually ramp weight decay alongside the l2 parameters
        l2_ramp = self._compute_ramp(hps.l2_start_epoch, hps.l2_increase_epoch)
        optimizer.param_groups[0]["weight_decay"] = l2_ramp * hps.weight_decay

    def _shared_step(self, batch, batch_idx, split):
        hps = self.hparams
        # Check that the split argument is valid
        assert split in ["train", "valid"]
        # Discard the extra data - only the SessionBatches are relevant here
        batch = batch[0]
        # Process the batch for each session (in order so aug stack can keep track)
        aug_stack = self.train_aug_stack if split == "train" else self.infer_aug_stack
        batch = aug_stack.process_batch(batch)
        # Perform the forward pass
        output = self.forward(
            batch, sample_posteriors=hps.variational, output_means=False
        )
        # Compute the reconstruction loss
        recon_all = self.recon.compute_loss(batch.recon_data, output.output_params)

        # Apply losses processing
        recon_all = aug_stack.process_losses(recon_all, batch, self.log, split)
        # Aggregate the heldout cost for logging
        if not hps.recon_reduce_mean:
            recon_all = torch.sum(recon_all, dim=(1, 2))
        # Compute reconstruction loss for each session
        recon = recon_all.mean()
        # Compute the L2 penalty on recurrent weights
        l2 = compute_l2_penalty(self, self.hparams)
        # Collect posterior parameters for fast KL calculation
        ic_mean = output.ic_mean
        ic_std = output.ic_std
        co_means = output.co_means
        co_stds = output.co_stds
        # Compute the KL penalty on posteriors
        ic_kl = self.ic_prior(ic_mean, ic_std) * self.hparams.kl_ic_scale
        co_kl = self.co_prior(co_means, co_stds) * self.hparams.kl_co_scale
        # Compute ramping coefficients
        l2_ramp = self._compute_ramp(hps.l2_start_epoch, hps.l2_increase_epoch)
        kl_ramp = self._compute_ramp(hps.kl_start_epoch, hps.kl_increase_epoch)
        # Compute the final loss
        loss = hps.loss_scale * (recon + l2_ramp * l2 + kl_ramp * (ic_kl + co_kl))
        # Compute the reconstruction accuracy, if applicable
        if batch.truth.numel() > 0:
            output_means = self.recon.compute_means(output.output_params)
            r2 = torch.mean(r2_score(output_means, batch.truth))
        else:
            r2 = float("nan")
        # Compute batch sizes for logging
        batch_size = len(batch.encod_data)
        # Log per-session metrics
        self.log(
            name=f"{split}/recon/",
            value=recon,
            on_step=False,
            on_epoch=True,
            batch_size=batch_size,
        )
        # Collect metrics for logging
        metrics = {
            f"{split}/loss": loss,
            f"{split}/recon": recon,
            f"{split}/r2": r2,
            f"{split}/wt_l2": l2,
            f"{split}/wt_l2/ramp": l2_ramp,
            f"{split}/wt_kl": ic_kl + co_kl,
            f"{split}/wt_kl/ic": ic_kl,
            f"{split}/wt_kl/co": co_kl,
            f"{split}/wt_kl/ramp": kl_ramp,
        }
        if split == "valid":
            # Update the smoothed reconstruction loss
            self.valid_recon_smth.update(recon, batch_size)
            # Add validation-only metrics
            metrics.update(
                {
                    "valid/recon_smth": self.valid_recon_smth,
                    "hp_metric": recon,
                    "cur_epoch": float(self.current_epoch),
                }
            )
        # Log overall metrics
        self.log_dict(
            metrics,
            on_step=False,
            on_epoch=True,
            batch_size=batch_size,
        )

        return loss

    def training_step(self, batch, batch_idx):
        return self._shared_step(batch, batch_idx, "train")

    def validation_step(self, batch, batch_idx):
        return self._shared_step(batch, batch_idx, "valid")

    def predict_step(self, batch, batch_ix, sample_posteriors=True):
        # Discard the extra data - only the SessionBatches are relevant here
        batch = batch[0]
        # Process the batch for each session
        batch = self.infer_aug_stack.process_batch(batch)
        # Reset to clear any saved masks
        self.infer_aug_stack.reset()
        # Perform the forward pass
        return self.forward(
            batch=batch,
            sample_posteriors=self.hparams.variational and sample_posteriors,
            output_means=True,
        )

    def on_validation_epoch_end(self):
        # Log hyperparameters that may change during PBT
        self.log_dict(
            {
                "hp/lr_init": self.hparams.lr_init,
                "hp/dropout_rate": self.hparams.dropout_rate,
                "hp/l2_ic_enc_scale": self.hparams.l2_ic_enc_scale,
                "hp/l2_ci_enc_scale": self.hparams.l2_ci_enc_scale,
                "hp/l2_gen_scale": self.hparams.l2_gen_scale,
                "hp/l2_con_scale": self.hparams.l2_con_scale,
                "hp/kl_co_scale": self.hparams.kl_co_scale,
                "hp/kl_ic_scale": self.hparams.kl_ic_scale,
                "hp/weight_decay": self.hparams.weight_decay,
            }
        )
        # Log CD rate if CD is being used
        for aug in self.train_aug_stack.batch_transforms:
            if hasattr(aug, "cd_rate"):
                self.log("hp/cd_rate", aug.cd_rate)
