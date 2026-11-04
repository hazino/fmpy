/* FMI 2.0 Co-Simulation wrapper around a third-party PID controller.
 *
 * The control algorithm (PID.c / PID.h) is unmodified third-party code:
 * https://github.com/pms67/PID (c) 2020 Philip Salmony, MIT License.
 * See LICENSE_PID.txt. Everything in THIS file is the thin FMI shim around
 * it -- value references, get/set, doStep -- written for this project.
 *
 * Exports every fmi2* symbol fmpy.fmi2.FMU2Slave loads (see
 * docs: it inspects every fmi2* method defined on _FMU2 + FMU2Slave and
 * resolves it in the shared library, regardless of whether the scenario
 * runner ever calls it). Functions this model has no real use for
 * (FMU state serialization, directional derivatives) are honest no-ops
 * that report "not implemented" rather than silently lying.
 */
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

#include "PID.h"

/* fmi2Functions.h defines FMI2_Export itself (dllexport on Windows) and
 * declares every function below with its exact official signature, so a
 * mismatched parameter type here would be a compile error, not a silent
 * ABI bug. */
#include "fmi2Functions.h"

/* ---- Value references: fixed layout for this model -------------------- */
#define VR_SETPOINT      0  /* Real, input                                 */
#define VR_MEASUREMENT   1  /* Real, input                                 */
#define VR_KP            2  /* Real, parameter (fixed)                     */
#define VR_KI            3
#define VR_KD            4
#define VR_TAU           5  /* derivative low-pass filter time constant    */
#define VR_LIMMIN        6  /* output lower limit                          */
#define VR_LIMMAX        7  /* output upper limit                          */
#define VR_LIMMININT      8  /* integrator lower limit (anti-windup)       */
#define VR_LIMMAXINT      9  /* integrator upper limit (anti-windup)       */
#define VR_T             10 /* sample time; should match communicationStepSize */
#define VR_OUTPUT        11 /* Real, output: pid.out                       */
#define VR_INTEGRATOR    12 /* Real, output: pid.integrator (diagnostic)   */
#define VR_DIFFERENTIATOR 13 /* Real, output: pid.differentiator (diagnostic) */
#define VR_ERROR         14 /* Real, output: setpoint - measurement        */
#define N_VARIABLES      15

typedef struct {
    fmi2CallbackFunctions callbacks;
    fmi2String instanceName;
    PIDController pid;
    double setpoint;
    double measurement;
    double error;
    double communicationStepSize; /* from the most recent doStep call, for the T mismatch check */
    int initialized;
} ModelInstance;

static void log_message(ModelInstance *m, fmi2Status status, const char *category, const char *message) {
    if (m->callbacks.logger != NULL) {
        m->callbacks.logger(m->callbacks.componentEnvironment, m->instanceName, status, category, message);
    }
}

/* ---- Creation and destruction ------------------------------------------ */

FMI2_Export const char* fmi2GetTypesPlatform(void) {
    return fmi2TypesPlatform;
}

FMI2_Export const char* fmi2GetVersion(void) {
    return "2.0";
}

FMI2_Export fmi2Status fmi2SetDebugLogging(fmi2Component c, fmi2Boolean loggingOn, size_t nCategories, const fmi2String categories[]) {
    (void)c; (void)loggingOn; (void)nCategories; (void)categories;
    return fmi2OK;
}

FMI2_Export fmi2Component fmi2Instantiate(fmi2String instanceName, fmi2Type fmuType, fmi2String fmuGUID,
                                          fmi2String fmuResourceLocation, const fmi2CallbackFunctions* functions,
                                          fmi2Boolean visible, fmi2Boolean loggingOn) {
    (void)fmuGUID; (void)fmuResourceLocation; (void)visible; (void)loggingOn;
    if (fmuType != fmi2CoSimulation) {
        return NULL; /* this model only implements the Co-Simulation interface */
    }
    ModelInstance *m = (ModelInstance *)calloc(1, sizeof(ModelInstance));
    if (m == NULL) {
        return NULL;
    }
    m->callbacks = *functions;
    m->instanceName = instanceName;
    /* Defaults match modelDescription.xml's declared start values. */
    m->pid.Kp = 2.0f; m->pid.Ki = 0.5f; m->pid.Kd = 0.1f; m->pid.tau = 0.02f;
    m->pid.limMin = -10.0f; m->pid.limMax = 10.0f;
    m->pid.limMinInt = -5.0f; m->pid.limMaxInt = 5.0f;
    m->pid.T = 0.01f;
    m->setpoint = 0.0; m->measurement = 0.0; m->error = 0.0;
    m->communicationStepSize = m->pid.T;
    PIDController_Init(&m->pid);
    return (fmi2Component)m;
}

FMI2_Export void fmi2FreeInstance(fmi2Component c) {
    free(c);
}

/* ---- Enter/exit initialization, terminate, reset ----------------------- */

FMI2_Export fmi2Status fmi2SetupExperiment(fmi2Component c, fmi2Boolean toleranceDefined, fmi2Real tolerance,
                                           fmi2Real startTime, fmi2Boolean stopTimeDefined, fmi2Real stopTime) {
    (void)c; (void)toleranceDefined; (void)tolerance; (void)startTime; (void)stopTimeDefined; (void)stopTime;
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2EnterInitializationMode(fmi2Component c) {
    (void)c;
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2ExitInitializationMode(fmi2Component c) {
    ModelInstance *m = (ModelInstance *)c;
    /* A real validity check on the controller's own configuration: anti-windup
     * and output clamping both assume limMin <= limMax. A misconfigured FMU
     * that violates this is refused here, with the reason in the FMU's log,
     * rather than producing a controller that clamps backwards. */
    if (m->pid.limMinInt > m->pid.limMaxInt) {
        log_message(m, fmi2Error, "logStatusError",
                   "limMinInt is greater than limMaxInt; the integrator anti-windup clamp would be inverted.");
        return fmi2Error;
    }
    if (m->pid.limMin > m->pid.limMax) {
        log_message(m, fmi2Error, "logStatusError",
                   "limMin is greater than limMax; the output clamp would be inverted.");
        return fmi2Error;
    }
    PIDController_Init(&m->pid);
    /* Compute one update from the initial inputs so the sample recorded at
     * start_time -- before any doStep -- is "calculated" (consistent with
     * setpoint/measurement) rather than a hardcoded zero. This matches the
     * initial="calculated" declared for output/integrator_state/
     * differentiator_state/error in modelDescription.xml. */
    m->error = m->setpoint - m->measurement;
    PIDController_Update(&m->pid, (float)m->setpoint, (float)m->measurement);
    m->initialized = 1;
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2Terminate(fmi2Component c) {
    (void)c;
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2Reset(fmi2Component c) {
    ModelInstance *m = (ModelInstance *)c;
    PIDController_Init(&m->pid);
    return fmi2OK;
}

/* ---- Getting and setting variable values -------------------------------- */

static fmi2Real get_real(ModelInstance *m, fmi2ValueReference vr) {
    switch (vr) {
        case VR_SETPOINT: return m->setpoint;
        case VR_MEASUREMENT: return m->measurement;
        case VR_KP: return (double)m->pid.Kp;
        case VR_KI: return (double)m->pid.Ki;
        case VR_KD: return (double)m->pid.Kd;
        case VR_TAU: return (double)m->pid.tau;
        case VR_LIMMIN: return (double)m->pid.limMin;
        case VR_LIMMAX: return (double)m->pid.limMax;
        case VR_LIMMININT: return (double)m->pid.limMinInt;
        case VR_LIMMAXINT: return (double)m->pid.limMaxInt;
        case VR_T: return (double)m->pid.T;
        case VR_OUTPUT: return (double)m->pid.out;
        case VR_INTEGRATOR: return (double)m->pid.integrator;
        case VR_DIFFERENTIATOR: return (double)m->pid.differentiator;
        case VR_ERROR: return m->error;
        default: return 0.0;
    }
}

static void set_real(ModelInstance *m, fmi2ValueReference vr, fmi2Real value) {
    switch (vr) {
        case VR_SETPOINT: m->setpoint = value; break;
        case VR_MEASUREMENT: m->measurement = value; break;
        case VR_KP: m->pid.Kp = (float)value; break;
        case VR_KI: m->pid.Ki = (float)value; break;
        case VR_KD: m->pid.Kd = (float)value; break;
        case VR_TAU: m->pid.tau = (float)value; break;
        case VR_LIMMIN: m->pid.limMin = (float)value; break;
        case VR_LIMMAX: m->pid.limMax = (float)value; break;
        case VR_LIMMININT: m->pid.limMinInt = (float)value; break;
        case VR_LIMMAXINT: m->pid.limMaxInt = (float)value; break;
        case VR_T: m->pid.T = (float)value; break;
        default: break; /* outputs are not writable; the scenario runner never tries */
    }
}

FMI2_Export fmi2Status fmi2GetReal(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, fmi2Real value[]) {
    ModelInstance *m = (ModelInstance *)c;
    for (size_t i = 0; i < nvr; i++) {
        value[i] = get_real(m, vr[i]);
    }
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2GetInteger(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, fmi2Integer value[]) {
    (void)c; (void)vr;
    for (size_t i = 0; i < nvr; i++) { value[i] = 0; }
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2GetBoolean(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, fmi2Boolean value[]) {
    (void)c; (void)vr;
    for (size_t i = 0; i < nvr; i++) { value[i] = fmi2False; }
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2GetString(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, fmi2String value[]) {
    (void)c; (void)vr;
    for (size_t i = 0; i < nvr; i++) { value[i] = ""; }
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2SetReal(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, const fmi2Real value[]) {
    ModelInstance *m = (ModelInstance *)c;
    for (size_t i = 0; i < nvr; i++) {
        set_real(m, vr[i], value[i]);
    }
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2SetInteger(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, const fmi2Integer value[]) {
    (void)c; (void)vr; (void)nvr; (void)value;
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2SetBoolean(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, const fmi2Boolean value[]) {
    (void)c; (void)vr; (void)nvr; (void)value;
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2SetString(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, const fmi2String value[]) {
    (void)c; (void)vr; (void)nvr; (void)value;
    return fmi2OK;
}

/* ---- FMU state: not supported by this model; say so honestly ----------- */

FMI2_Export fmi2Status fmi2GetFMUstate(fmi2Component c, fmi2FMUstate* s) { (void)c; *s = NULL; return fmi2Error; }
FMI2_Export fmi2Status fmi2SetFMUstate(fmi2Component c, fmi2FMUstate s) { (void)c; (void)s; return fmi2Error; }
FMI2_Export fmi2Status fmi2FreeFMUstate(fmi2Component c, fmi2FMUstate* s) { (void)c; (void)s; return fmi2OK; }
FMI2_Export fmi2Status fmi2SerializedFMUstateSize(fmi2Component c, fmi2FMUstate s, size_t* size) { (void)c; (void)s; *size = 0; return fmi2Error; }
FMI2_Export fmi2Status fmi2SerializeFMUstate(fmi2Component c, fmi2FMUstate s, fmi2Byte buf[], size_t size) { (void)c; (void)s; (void)buf; (void)size; return fmi2Error; }
FMI2_Export fmi2Status fmi2DeSerializeFMUstate(fmi2Component c, const fmi2Byte buf[], size_t size, fmi2FMUstate* s) { (void)c; (void)buf; (void)size; *s = NULL; return fmi2Error; }

FMI2_Export fmi2Status fmi2GetDirectionalDerivative(fmi2Component c, const fmi2ValueReference vUnknown[], size_t nUnknown,
                                                    const fmi2ValueReference vKnown[], size_t nKnown,
                                                    const fmi2Real dvKnown[], fmi2Real dvUnknown[]) {
    (void)c; (void)vUnknown; (void)nUnknown; (void)vKnown; (void)nKnown; (void)dvKnown; (void)dvUnknown;
    return fmi2Error; /* not provided; declared as such in modelDescription.xml */
}

/* ---- Co-Simulation: stepping -------------------------------------------- */

FMI2_Export fmi2Status fmi2SetRealInputDerivatives(fmi2Component c, const fmi2ValueReference vr[], size_t nvr,
                                                   const fmi2Integer order[], const fmi2Real value[]) {
    (void)c; (void)vr; (void)nvr; (void)order; (void)value;
    return fmi2Error; /* no input derivatives supported */
}

FMI2_Export fmi2Status fmi2GetRealOutputDerivatives(fmi2Component c, const fmi2ValueReference vr[], size_t nvr,
                                                    const fmi2Integer order[], fmi2Real value[]) {
    (void)c; (void)vr; (void)order;
    for (size_t i = 0; i < nvr; i++) { value[i] = 0.0; }
    return fmi2Error; /* no output derivatives supported */
}

FMI2_Export fmi2Status fmi2DoStep(fmi2Component c, fmi2Real currentCommunicationPoint, fmi2Real communicationStepSize,
                                  fmi2Boolean noSetFMUStatePriorToCurrentPoint) {
    (void)currentCommunicationPoint; (void)noSetFMUStatePriorToCurrentPoint;
    ModelInstance *m = (ModelInstance *)c;
    m->communicationStepSize = communicationStepSize;
    /* The controller's own sample time T feeds directly into its integral and
     * derivative terms (see PID.c). If the scenario's communication grid does
     * not match it, the computed response is for the wrong sample time -- a
     * genuine configuration mistake, not a numerical nuisance, so it is
     * reported through the FMU's own logger exactly as a real exported model
     * would. */
    if (fabs((double)m->pid.T - communicationStepSize) > 1e-9) {
        char message[256];
        snprintf(message, sizeof(message),
                "communicationStepSize (%.6g) does not match parameter T (%.6g); "
                "the integral and derivative terms are computed for the wrong sample time.",
                communicationStepSize, (double)m->pid.T);
        log_message(m, fmi2Warning, "logStatusError", message);
    }
    m->error = m->setpoint - m->measurement;
    PIDController_Update(&m->pid, (float)m->setpoint, (float)m->measurement);
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2CancelStep(fmi2Component c) {
    (void)c;
    return fmi2OK;
}

/* ---- Inquire slave status ------------------------------------------------ */

FMI2_Export fmi2Status fmi2GetStatus(fmi2Component c, const fmi2StatusKind s, fmi2Status* value) {
    (void)c; (void)s;
    *value = fmi2OK;
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2GetRealStatus(fmi2Component c, const fmi2StatusKind s, fmi2Real* value) {
    (void)c; (void)s;
    *value = 0.0;
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2GetIntegerStatus(fmi2Component c, const fmi2StatusKind s, fmi2Integer* value) {
    (void)c; (void)s;
    *value = 0;
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2GetBooleanStatus(fmi2Component c, const fmi2StatusKind s, fmi2Boolean* value) {
    (void)c; (void)s;
    *value = fmi2False;
    return fmi2OK;
}

FMI2_Export fmi2Status fmi2GetStringStatus(fmi2Component c, const fmi2StatusKind s, fmi2String* value) {
    (void)c; (void)s;
    *value = "";
    return fmi2OK;
}
