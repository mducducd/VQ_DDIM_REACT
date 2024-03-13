import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np


class KLLoss(nn.Module):
    def __init__(self):
        super(KLLoss, self).__init__()

    def forward(self, q, p):
        div = torch.distributions.kl_divergence(q, p)
        return div.mean()

    def __repr__(self):
        return "KLLoss()"



class VAELoss(nn.Module):
    def __init__(self, kl_p=0.0002):
        super(VAELoss, self).__init__()
        self.mse = nn.MSELoss(reduce=True, size_average=True)
        self.kl_loss = KLLoss()
        self.kl_p = kl_p

    def forward(self, gt_emotion, gt_3dmm, pred_emotion, pred_3dmm, distribution):
        rec_loss = self.mse(pred_emotion, gt_emotion) + self.mse(pred_3dmm[:,:, :52], gt_3dmm[:,:, :52]) + 10*self.mse(pred_3dmm[:,:, 52:], gt_3dmm[:,:, 52:])
        mu_ref = torch.zeros_like(distribution[0].loc).to(gt_emotion.get_device())
        scale_ref = torch.ones_like(distribution[0].scale).to(gt_emotion.get_device())
        distribution_ref = torch.distributions.Normal(mu_ref, scale_ref)

        kld_loss = 0
        for t in range(len(distribution)):
            kld_loss += self.kl_loss(distribution[t], distribution_ref)
        kld_loss = kld_loss / len(distribution)

        loss = rec_loss + self.kl_p * kld_loss


        return loss, rec_loss, kld_loss

    def __repr__(self):
        return "VAELoss()"



def div_loss(Y_1, Y_2):
    loss = 0.0
    b,t,c = Y_1.shape
    Y_g = torch.cat([Y_1.view(b,1,-1), Y_2.view(b,1,-1)], dim = 1)
    for Y in Y_g:
        dist = F.pdist(Y, 2) ** 2
        loss += (-dist /  100).exp().mean()
    loss /= b
    return loss




# ================================ BeLFUSION losses ====================================

def MSELoss_AE_v2(prediction, target, target_coefficients, mu, logvar, coefficients_3dmm, 
                  w_mse=1, w_kld=1, w_coeff=1, 
                  **kwargs):
    # loss for autoencoder. prediction and target have shape of [batch_size, seq_length, features]
    assert len(prediction.shape) == len(target.shape), "prediction and target must have the same shape"
    assert len(prediction.shape) == 3, "Only works with predictions of shape [batch_size, seq_length, features]"
    batch_size = prediction.shape[0]

    # join last two dimensions of prediction and target
    prediction = prediction.reshape(prediction.shape[0], -1)
    target = target.reshape(target.shape[0], -1)
    coefficients_3dmm = coefficients_3dmm.reshape(coefficients_3dmm.shape[0], -1)
    target_coefficients = target_coefficients.reshape(target_coefficients.shape[0], -1)

    MSE = ((prediction - target) ** 2).mean()
    KLD = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp()) / batch_size
    COEFF = ((coefficients_3dmm - target_coefficients) ** 2).mean()

    loss_r = w_mse * MSE + w_kld * KLD + w_coeff * COEFF
    return {"loss": loss_r, "mse": MSE, "kld": KLD, "coeff": COEFF}

def MSELoss_AE_v2_VQ(prediction, target, target_coefficients, coefficients_3dmm, 
                  w_mse=1, w_coeff=1, 
                  **kwargs):
    # loss for autoencoder. prediction and target have shape of [batch_size, seq_length, features]
    assert len(prediction.shape) == len(target.shape), "prediction and target must have the same shape"
    assert len(prediction.shape) == 3, "Only works with predictions of shape [batch_size, seq_length, features]"
    batch_size = prediction.shape[0]

    # join last two dimensions of prediction and target
    prediction = prediction.reshape(prediction.shape[0], -1)
    target = target.reshape(target.shape[0], -1)
    coefficients_3dmm = coefficients_3dmm.reshape(coefficients_3dmm.shape[0], -1)
    target_coefficients = target_coefficients.reshape(target_coefficients.shape[0], -1)

    MSE = ((prediction - target) ** 2).mean()
    # MSE = concordance_correlation_coefficient_loss(prediction, target) #change to CCC
    COEFF = ((coefficients_3dmm - target_coefficients) ** 2).mean()

    loss_r = w_mse * MSE + w_coeff * COEFF
    return {"loss": loss_r, "mse": MSE, "loss_r": loss_r, "coeff": COEFF}

def MSELoss(prediction, target, reduction="mean", **kwargs):
    # prediction has shape of [batch_size, num_preds, features]
    # target has shape of [batch_size, num_preds, features]
    assert len(prediction.shape) == len(target.shape), "prediction and target must have the same shape"
    assert len(prediction.shape) == 3, "Only works with predictions of shape [batch_size, num_preds, features]"

    # manual implementation of MSE loss
    loss = ((prediction - target) ** 2).mean(axis=-1)
    
    # reduce across multiple predictions
    if reduction == "mean":
        loss = torch.mean(loss)
    elif reduction == "min":
        loss = loss.min(axis=-1)[0].mean()
    else:
        raise NotImplementedError("reduction {} not implemented".format(reduction))
    return loss


def L1Loss(prediction, target, reduction="mean", **kwargs):
    # prediction has shape of [batch_size, num_preds, features]
    # target has shape of [batch_size, num_preds, features]
    assert len(prediction.shape) == len(target.shape), "prediction and target must have the same shape"
    assert len(prediction.shape) == 3, "Only works with predictions of shape [batch_size, num_preds, features]"

    # manual implementation of L1 loss
    loss = (torch.abs(prediction - target)).mean(axis=-1)
    
    # reduce across multiple predictions
    if reduction == "mean":
        loss = torch.mean(loss)
    elif reduction == "min":
        loss = loss.min(axis=-1)[0].mean()
    else:
        raise NotImplementedError("reduction {} not implemented".format(reduction))
    return loss


def BeLFusionLoss(prediction, target, encoded_prediction, encoded_target, 
                  losses = [L1Loss, MSELoss], 
                  losses_multipliers = [1, 1], 
                  losses_decoded = [False, True], 
                  **kwargs):
    # encoded_prediction has shape of [batch_size, num_preds, features]
    # encoded_target has shape of [batch_size, num_preds, features]
    # prediction has shape of [batch_size, num_preds, seq_len, features]
    # target has shape of [batch_size, num_preds, seq_len, features]
    assert len(losses) == len(losses_multipliers), "losses and losses_multipliers must have the same length"
    assert len(losses) == len(losses_decoded), "losses and losses_decoded must have the same length"
    #assert len(encoded_prediction.shape) == 3 and len(prediction.shape) == 4, "BeLFusionLoss only works with multiple predictions"

    if len(encoded_prediction.shape) == 2 and len(prediction.shape) == 3: # --> for the test script to work, only a single pred is used
        # unsqueeze the first dimension, because we only have one prediction
        prediction = prediction.unsqueeze(1)
        target = target.unsqueeze(1)
        encoded_prediction = encoded_prediction.unsqueeze(1)
        encoded_target = encoded_target.unsqueeze(1)

    if len(encoded_prediction.shape) == 3 and len(prediction.shape) == 4:
        # join last two dimensions of prediction and target
        prediction = prediction.reshape(prediction.shape[0], prediction.shape[1], -1)
        target = target.reshape(target.shape[0], target.shape[1], -1)
    else:
        raise NotImplementedError("BeLFusionLoss only works with multiple predictions")

    # compute losses
    losses_dict = {"loss": 0}
    for loss_name, w, decoded in zip(losses, losses_multipliers, losses_decoded):
        loss_final_name = loss_name + f"_{'decoded' if decoded else 'encoded'}"
        if decoded:
            losses_dict[loss_final_name] = eval(loss_name)(prediction, target, reduction="min")
        else:
            losses_dict[loss_final_name] = eval(loss_name)(encoded_prediction, encoded_target, reduction="min")
    
        losses_dict["loss"] += losses_dict[loss_final_name] * w

    return losses_dict

def corrcoef(x, y=None, rowvar=True, bias=np._NoValue, ddof=np._NoValue, *,
             dtype=None):

    if bias is not np._NoValue or ddof is not np._NoValue:
        # 2015-03-15, 1.10
        warnings.warn('bias and ddof have no effect and are deprecated',
                      DeprecationWarning, stacklevel=3)
    c = np.cov(x, y, rowvar, dtype=dtype)

    try:
        d = np.diag(c)
    except ValueError:
        # scalar covariance
        # nan if incorrect value (nan, inf, 0), 1 otherwise
        return c / c
    stddev = np.sqrt(d.real)
    c /= stddev[:, None]
    c /= stddev[None, :]
    c = np.nan_to_num(c)

    # Clip real and imaginary parts to [-1, 1].  This does not guarantee
    # abs(a[i,j]) <= 1 for complex arrays, but is the best we can do without
    # excessive work.
    np.clip(c.real, -1, 1, out=c.real)
    if np.iscomplexobj(c):
        np.clip(c.imag, -1, 1, out=c.imag)
    return c

def concordance_correlation_coefficient_loss(y_true, y_pred,
                                        sample_weight=None,
                                        multioutput='uniform_average'):
    """Concordance correlation coefficient.
    The concordance correlation coefficient is a measure of inter-rater agreement.
    It measures the deviation of the relationship between predicted and true values
    from the 45 degree angle.
    Read more: https://en.wikipedia.org/wiki/Concordance_correlation_coefficient
    Original paper: Lawrence, I., and Kuei Lin. "A concordance correlation coefficient to evaluate reproducibility." Biometrics (1989): 255-268.
    Parameters
    ----------
    y_true : array-like of shape = (n_samples) or (n_samples, n_outputs)
        Ground truth (correct) target values.
    y_pred : array-like of shape = (n_samples) or (n_samples, n_outputs)
        Estimated target values.
    Returns
    -------
    loss : A float in the range [-1,1]. A value of 1 indicates perfect agreement
    between the true and the predicted values.
    Examples
    --------
    >>> from sklearn.metrics import concordance_correlation_coefficient
    >>> y_true = [3, -0.5, 2, 7]
    >>> y_pred = [2.5, 0.0, 2, 8]
    >>> concordance_correlation_coefficient(y_true, y_pred)
    0.97678916827853024
    """
    if len(y_true.shape) >1:
        ccc_list = []
        for i in range(y_true.shape[1]):
            # cor = corrcoef(y_true[:,i].cpu().detach().numpy(), y_pred[:, i].cpu().detach().numpy())[0][1]
            # cor = np.corrcoef(y_true[:,i].cpu().detach().numpy(),y_pred[:, i].cpu().detach().numpy())[0][1]
            mean_true = torch.mean(y_true[:,i])

            mean_pred = torch.mean(y_pred[:,i])


            var_true = torch.var(y_true[:,i])
            var_pred = torch.var(y_pred[:,i])

            sd_true = torch.std(y_true[:,i])
            sd_pred = torch.std(y_pred[:,i])

            # numerator = 2 * cor * sd_true * sd_pred
            numerator = 2 * sd_true * sd_pred

            denominator = var_true + var_pred + (mean_true - mean_pred) ** 2

            ccc = 1 - (numerator / (denominator + 1e-8))
            
            ccc_list.append(ccc)
        ccc = torch.mean(torch.as_tensor(ccc_list))
    else:
        cor = np.corrcoef(y_true, y_pred)[0][1]
        mean_true = np.mean(y_true)
        mean_pred = np.mean(y_pred)

        var_true = np.var(y_true)
        var_pred = np.var(y_pred)

        sd_true = np.std(y_true)
        sd_pred = np.std(y_pred)

        numerator = 2 * cor * sd_true * sd_pred

        denominator = var_true + var_pred + (mean_true - mean_pred) ** 2
        ccc = numerator / (denominator + 1e-8)
    return (ccc+1)/2