import numpy as np
from casadi import MX, Function, horzcat
from math import *
from bioptim import Data
import biorbd
import csv
import warnings
import scipy
import scipy.fftpack


def markers_fun(biorbd_model):
    qMX = MX.sym('qMX', biorbd_model.nbQ())
    return Function('markers', [qMX],
                    [horzcat(*[biorbd_model.markers(qMX)[i].to_mx() for i in range(biorbd_model.nbMarkers())])])


def muscles_forces(q, qdot, act, controls, model, use_activation=False):
    muscles_states = biorbd.VecBiorbdMuscleState(model.nbMuscles())
    for k in range(model.nbMuscles()):
        if use_activation:
            muscles_states[k].setActivation(controls[k])
        else:
            muscles_states[k].setExcitation(controls[k])
            muscles_states[k].setActivation(act[k])
    # muscles_tau.append(model.muscularJointTorque(muscles_states, q, qdot).to_mx())
    muscles_force = model.muscleForces(muscles_states, q, qdot).to_mx()
    return muscles_force


def force_func(biorbd_model, use_activation=False):
    qMX = MX.sym("qMX", biorbd_model.nbQ(), 1)
    dqMX = MX.sym("dqMX", biorbd_model.nbQ(), 1)
    aMX = MX.sym("aMX", biorbd_model.nbMuscles(), 1)
    uMX = MX.sym("uMX", biorbd_model.nbMuscles(), 1)
    return Function("MuscleForce", [qMX, dqMX, aMX, uMX],
                    [muscles_forces(qMX, dqMX, aMX, uMX, biorbd_model, use_activation=use_activation)],
                    ["qMX", "dqMX", "aMX", "uMX"], ["Force"]).expand()


def compute_err_mhe(init_offset, final_offset, Ns_mhe, X_est, U_est, Ns, model, q, dq, tau,
                activations, excitations, nbGT, ratio=1, use_activation=False):
    model = model
    get_force = force_func(model, use_activation=use_activation)
    get_markers = markers_fun(model)
    err = dict()
    offset = final_offset - Ns_mhe
    nbGT = nbGT
    Ns = Ns
    q_ref = q[:, 0:Ns+1:ratio]
    dq_ref = dq[:, 0:Ns+1:ratio]
    tau_ref = tau[:, 0:Ns:ratio]
    if use_activation:
        muscles_ref = activations[:, 0:Ns:ratio]
    else:
        muscles_ref = excitations[nbGT:, 0:Ns:ratio]
    sol_mark = np.zeros((3, model.nbMarkers(), ceil((Ns + 1) / ratio) - Ns_mhe))
    sol_mark_ref = np.zeros((3, model.nbMarkers(), ceil((Ns + 1) / ratio) - Ns_mhe))
    err['q'] = np.sqrt(np.square(X_est[:model.nbQ(), init_offset:-offset] - q_ref[:, init_offset:-final_offset]).mean(axis=1)).mean()
    err['q_dot'] = np.sqrt(
        np.square(X_est[model.nbQ():model.nbQ() * 2, init_offset:-offset] - dq_ref[:, init_offset:-final_offset]).mean(axis=1)).mean()
    err['tau'] = np.sqrt(np.square(U_est[:nbGT, init_offset:-offset] - tau_ref[:nbGT, init_offset:-final_offset]).mean(axis=1)).mean()
    err['muscles'] = np.sqrt(np.square(U_est[nbGT:, init_offset:-offset] - muscles_ref[:, init_offset:-final_offset]).mean(axis=1)).mean()
    for i in range(ceil((Ns + 1) / ratio) - Ns_mhe):
        sol_mark[:, :, i] = get_markers(X_est[:model.nbQ(), i])
    sol_mark_tmp = np.zeros((3, sol_mark_ref.shape[1], Ns + 1))
    for i in range(Ns + 1):
        sol_mark_tmp[:, :, i] = get_markers(q[:, i])
    sol_mark_ref = sol_mark_tmp[:, :, 0:Ns + 1:ratio]
    err['markers'] = np.sqrt(np.square(sol_mark[:, :, init_offset:-offset] - sol_mark_ref[:, :, init_offset:-final_offset]).sum(axis=0).mean(axis=1)).mean()

    force_ref_tmp = np.ndarray((model.nbMuscles(), Ns ))
    force_est = np.ndarray((model.nbMuscles(), int(ceil(Ns / ratio) - Ns_mhe)))
    if use_activation:
        a_est = np.zeros((model.nbMuscles(), Ns))
    else:
        a_est = X_est[-model.nbMuscles():, :]

    for i in range(model.nbMuscles()):
        for j in range(int(ceil(Ns / ratio) - Ns_mhe)):
            force_est[i, j] = get_force(
                X_est[:model.nbQ(), j], X_est[model.nbQ():model.nbQ()*2, j], a_est[:, j],
                U_est[nbGT:, j]
            )[i, :]
    get_force = force_func(model, use_activation=False)
    for i in range(model.nbMuscles()):
        for k in range(Ns):
            force_ref_tmp[i, k] = get_force(q[:, k], dq[:, k], activations[:, k], excitations[nbGT:, k])[i, :]
    force_ref = force_ref_tmp[:, 0:Ns:ratio]
    err['force'] = np.sqrt(np.square(force_est[:, init_offset:-offset] - force_ref[:, init_offset:-final_offset]).mean(axis=1)).mean()

    return err


def compute_err(init_offset, final_offset, X_est, U_est, Ns, model, q, dq, tau,
                activations, excitations, nbGT, use_activation=False):
    model = model
    get_markers = markers_fun(model)
    err = dict()
    nbGT = nbGT
    Ns = Ns
    q_ref = q[:, 0:Ns + 1]
    dq_ref = dq[:, 0:Ns + 1]
    tau_ref = tau[:, 0:Ns]
    muscles_ref = excitations[:, 0:Ns]
    if use_activation:
        muscles_ref = activations[:, 0:Ns]
    sol_mark = np.zeros((3, model.nbMarkers(), Ns + 1))
    sol_mark_ref = np.zeros((3, model.nbMarkers(), Ns + 1))
    err['q'] = np.sqrt(np.square(X_est[:model.nbQ(), init_offset:-final_offset] - q_ref[:, init_offset:-final_offset]).mean(axis=1)).mean()
    err['q_dot'] = np.sqrt(
        np.square(X_est[model.nbQ():model.nbQ() * 2, init_offset:-final_offset] - dq_ref[:, init_offset:-final_offset]).mean(axis=1)).mean()
    err['tau'] = np.sqrt(np.square(U_est[:nbGT, init_offset:-final_offset-1] - tau_ref[:, init_offset:-final_offset]).mean(axis=1)).mean()
    err['muscles'] = np.sqrt(np.square(U_est[nbGT:, init_offset:-final_offset-1] - muscles_ref[:, init_offset:-final_offset]).mean(axis=1)).mean()
    for i in range(Ns + 1):
        sol_mark[:, :, i] = get_markers(X_est[:model.nbQ(), i])
    sol_mark_tmp = np.zeros((3, sol_mark_ref.shape[1], Ns + 1))
    for i in range(Ns + 1):
        sol_mark_tmp[:, :, i] = get_markers(q[:, i])
    sol_mark_ref = sol_mark_tmp[:, :, 0:Ns + 1]
    err['markers'] = np.sqrt(np.square(sol_mark[:, :, init_offset:-final_offset] - sol_mark_ref[:, :, init_offset:-final_offset]).sum(axis=0).mean(axis=1)).mean()
    force_ref_tmp = np.ndarray((model.nbMuscles(), Ns))
    force_est = np.ndarray((model.nbMuscles(), Ns))
    if use_activation:
        a_est = np.zeros((model.nbMuscles(), Ns))
    else:
        a_est = X_est[-model.nbMuscles():, :]

    get_force = force_func(model, use_activation=use_activation)
    for i in range(model.nbMuscles()):
        for j in range(Ns):
            force_est[i, j] = get_force(
                X_est[:model.nbQ(), j], X_est[model.nbQ():model.nbQ() * 2, j], a_est[:, j],
                U_est[nbGT:, j]
            )[i, :]

    get_force = force_func(model, use_activation=False)
    for i in range(model.nbMuscles()):
        for k in range(Ns):
            force_ref_tmp[i, k] = get_force(q[:, k], dq[:, k], activations[:, k], excitations[:, k])[i, :]
    force_ref = force_ref_tmp[:, 0:Ns]
    err['force'] = np.sqrt(
        np.square(force_est[:, init_offset:-final_offset] - force_ref[:, init_offset:-final_offset]).mean(axis=1)).mean()

    return err


def warm_start_mhe(ocp, sol, use_activation=False):
    data = Data.get_data(ocp, sol)
    q = data[0]["q"]
    dq = data[0]["q_dot"]
    tau = []
    exc = []
    u = []
    w_tau = 'tau' in data[1].keys()
    if use_activation:
        act = data[1]["muscles"]
        x = np.vstack([q, dq])
        if w_tau:
            u0 = np.vstack([tau, act])[:, 1:]
            u = np.vstack([tau, act])
        else:
            u0 = act[:, 1:]
            u = act
    else:
        act = data[0]["muscles"]
        exc = data[1]["muscles"]
        x = np.vstack([q, dq, act])
        if w_tau:
            u0 = np.vstack([tau, act])[:, 1:] # take activation as initial guess for next optimization
            u = np.vstack([tau, exc])
        else:
            u0 = act[:, 1:] # take activation as initial guess for next optimization
            u = exc

    x0 = np.hstack((x[:, 1:], np.tile(x[:, [-1]], 1)))  # discard oldest estimate of the window, duplicates youngest
    # u0 = u[:, :-1]
    x_out = x[:, 0]
    u_out = u[:, 0]
    return x0, u0, x_out, u_out


def get_MHE_time_lenght(Ns_mhe, use_activation=False):
    # Nmhe>2
    # To be adjusted to guarantee real-time
    # Based on frequencies extracted from Fig.1
    if use_activation is not True:
        times_lenght = [0.024, 0.024, 0.024, 0.024,  # 1 sample on 3
                        0.032, 0.032,  # 1 sample on 4
                        0.04, 0.044,  # 1 sample on 5
                        0.048, 0.048, 0.048, 0.048, 0.048, 0.048,  # 1 sample on 6
                        0.056,  # 1 sample on 7
                        0.064, 0.064,  # 1 sample on 8
                        1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    return times_lenght[Ns_mhe-2]


def convert_txt_output_to_list(file, nbco, nbmark, nbemg, nbtries):
    conv_list = [[[[[] for i in range(nbtries)] for j in range(nbemg)] for k in range(nbmark)] for l in range(nbco)]
    with open(file) as f:
        fdel = csv.reader(f, delimiter=';', lineterminator='\n')
        for line in fdel:
            if line[0] == '7':
                try:
                    conv_list[int(line[1])][int(line[2])][int(line[3])][int(line[4])].append(line[5])
                except:
                    warnings.warn(f'line {line} ignored')
    return conv_list


def generate_noise(model, q, excitations, marker_noise_level, EMG_noise_level):
    biorbd_model = model
    q_sol = q
    u_co = excitations
    EMG_fft = scipy.fftpack.fft(u_co)
    EMG_no_noise = scipy.fftpack.ifft(EMG_fft)
    EMG_fft_noise = EMG_fft
    for k in range(biorbd_model.nbMuscles()):
        # EMG_fft_noise[k, 0] += np.random.normal(0, (np.real(EMG_fft_noise[k, 0]*0.2)))
        for i in range(1, 17, 3):
            if i in [4, 8]:
                rand_noise = np.random.normal(np.real(EMG_fft[k, i]) / i * EMG_noise_level,
                                              np.abs(np.real(EMG_fft[k, i]) * 0.2 * EMG_noise_level))

            elif i % 2 == 0:
                rand_noise = np.random.normal(2 * np.real(EMG_fft[k, i]) / i * EMG_noise_level,
                                              np.abs(np.real(EMG_fft[k, i]) * 0.2 * EMG_noise_level))

            else:
                rand_noise = np.random.normal(2 * np.real(EMG_fft[k, i]) / i * EMG_noise_level,
                                              np.abs(np.real(EMG_fft[k, i]) * EMG_noise_level * 5))
            EMG_fft_noise[k, i] += rand_noise
            EMG_fft_noise[k, -i] += rand_noise
    EMG_noise = np.real(scipy.fftpack.ifft(EMG_fft_noise))

    for i in range(biorbd_model.nbMuscles()):
        for j in range(EMG_noise.shape[1]):
            if EMG_noise[i, j] < 0:
                EMG_noise[i, j] = 0

    # Ref
    n_mark = biorbd_model.nbMarkers()
    for i in range(n_mark):
        noise_position = MX(np.random.normal(0, marker_noise_level, 3)) + biorbd_model.marker(i).to_mx()
        biorbd_model.marker(i).setPosition(biorbd.Vector3d(noise_position[0], noise_position[1], noise_position[2]))

    get_markers = markers_fun(biorbd_model)
    markers_target_noise = np.zeros((3, biorbd_model.nbMarkers(), q_sol.shape[1]))
    for i in range(q_sol.shape[1]):
        markers_target_noise[:, :, i] = get_markers(q_sol[:, i])

    return markers_target_noise, EMG_noise