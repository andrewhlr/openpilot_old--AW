import numpy as np
from common.kalman.simple_kalman import KF1D
from selfdrive.can.parser import CANParser, CANDefine
from selfdrive.config import Conversions as CV
from selfdrive.car.toyota.values import CAR, DBC, STEER_THRESHOLD
from common.kalman.simple_kalman import KF1D
from selfdrive.car.modules.UIBT_module import UIButtons,UIButton
from selfdrive.car.modules.UIEV_module import UIEvents
import numpy as np
def parse_gear_shifter(gear, vals):

  val_to_capnp = {'P': 'park', 'R': 'reverse', 'N': 'neutral',
                  'D': 'drive', 'B': 'brake'}
  try:
    return val_to_capnp[vals[gear]]
  except KeyError:
    return "unknown"


def get_can_parser(CP):

  signals = [
    # sig_name, sig_address, default
    ("GEAR", "GEAR_PACKET", 0),
    ("BRAKE_PRESSED", "BRAKE_MODULE", 0),
    ("GAS_PEDAL", "GAS_PEDAL", 0),
    ("WHEEL_SPEED_FL", "WHEEL_SPEEDS", 0),
    ("WHEEL_SPEED_FR", "WHEEL_SPEEDS", 0),
    ("WHEEL_SPEED_RL", "WHEEL_SPEEDS", 0),
    ("WHEEL_SPEED_RR", "WHEEL_SPEEDS", 0),
    ("DOOR_OPEN_FL", "SEATS_DOORS", 1),
    ("DOOR_OPEN_FR", "SEATS_DOORS", 1),
    ("DOOR_OPEN_RL", "SEATS_DOORS", 1),
    ("DOOR_OPEN_RR", "SEATS_DOORS", 1),
    ("SEATBELT_DRIVER_UNLATCHED", "SEATS_DOORS", 1),
    ("TC_DISABLED", "ESP_CONTROL", 1),
    ("STEER_ANGLE", "STEER_ANGLE_SENSOR", 0),
    ("STEER_FRACTION", "STEER_ANGLE_SENSOR", 0),
    ("STEER_RATE", "STEER_ANGLE_SENSOR", 0),
    ("GAS_RELEASED", "PCM_CRUISE", 0),
    ("CRUISE_STATE", "PCM_CRUISE", 0),
    ("MAIN_ON", "PCM_CRUISE_2", 0),
    ("SET_SPEED", "PCM_CRUISE_2", 0),
    ("LOW_SPEED_LOCKOUT", "PCM_CRUISE_2", 0),
    ("STEER_TORQUE_DRIVER", "STEER_TORQUE_SENSOR", 0),
    ("STEER_TORQUE_EPS", "STEER_TORQUE_SENSOR", 0),
    ("TURN_SIGNALS", "STEERING_LEVERS", 3),   # 3 is no blinkers
    ("LKA_STATE", "EPS_STATUS", 0),
    ("IPAS_STATE", "EPS_STATUS", 1),
    ("BRAKE_LIGHTS_ACC", "ESP_CONTROL", 0),
    ("AUTO_HIGH_BEAM", "LIGHT_STALK", 0),
  ]

  checks = [
    ("BRAKE_MODULE", 40),
    ("GAS_PEDAL", 33),
    ("WHEEL_SPEEDS", 80),
    ("STEER_ANGLE_SENSOR", 80),
    ("PCM_CRUISE", 33),
    ("PCM_CRUISE_2", 33),
    ("STEER_TORQUE_SENSOR", 50),
    ("EPS_STATUS", 25),
  ]

  if CP.carFingerprint == CAR.PRIUS:
    signals += [("STATE", "AUTOPARK_STATUS", 0)]

  return CANParser(DBC[CP.carFingerprint]['pt'], signals, checks, 0)


class CarState(object):
  def __init__(self, CP):

    self.CP = CP
    self.can_define = CANDefine(DBC[CP.carFingerprint]['pt'])
    self.shifter_values = self.can_define.dv["GEAR_PACKET"]['GEAR']
    self.left_blinker_on = 0
    self.right_blinker_on = 0
    #BB UIEvents
    self.UE = UIEvents(self)

    #BB variable for custom buttons
    self.cstm_btns = UIButtons(self,"Toyota","toyota")

    #BB pid holder for ALCA
    self.pid = None

    #BB custom message counter
    self.custom_alert_counter = -1 #set to 100 for 1 second display; carcontroller will take down to zero

    # initialize can parser
    self.car_fingerprint = CP.carFingerprint

    # vEgo kalman filter
    dt = 0.01
    # Q = np.matrix([[10.0, 0.0], [0.0, 100.0]])
    # R = 1e3
    self.v_ego_kf = KF1D(x0=np.matrix([[0.0], [0.0]]),
                         A=np.matrix([[1.0, dt], [0.0, 1.0]]),
                         C=np.matrix([1.0, 0.0]),
                         K=np.matrix([[0.12287673], [0.29666309]]))
    self.v_ego = 0.0
  
  #BB init ui buttons
  def init_ui_buttons(self):
    btns = []
    btns.append(UIButton("alca", "ALC", 0, "", 0))
    btns.append(UIButton("", "", 0, "", 1))
    btns.append(UIButton("", "", 0, "", 2))
    btns.append(UIButton("sound", "SND", 1, "", 3))
    btns.append(UIButton("", "", 0, "", 4))
    btns.append(UIButton("", "", 0, "", 5))
    return btns

  #BB update ui buttons
  def update_ui_buttons(self,id,btn_status):
    if self.cstm_btns.btns[id].btn_status > 0:
        self.cstm_btns.btns[id].btn_status = btn_status * self.cstm_btns.btns[id].btn_status
    else:
        self.cstm_btns.btns[id].btn_status = btn_status

  def update(self, cp):
    # copy can_valid
    self.can_valid = cp.can_valid

    # update prevs, update must run once per loop
    self.prev_left_blinker_on = self.left_blinker_on
    self.prev_right_blinker_on = self.right_blinker_on

    self.door_all_closed = not any([cp.vl["SEATS_DOORS"]['DOOR_OPEN_FL'], cp.vl["SEATS_DOORS"]['DOOR_OPEN_FR'],
                                    cp.vl["SEATS_DOORS"]['DOOR_OPEN_RL'], cp.vl["SEATS_DOORS"]['DOOR_OPEN_RR']])
    self.seatbelt = not cp.vl["SEATS_DOORS"]['SEATBELT_DRIVER_UNLATCHED']

    self.brake_pressed = cp.vl["BRAKE_MODULE"]['BRAKE_PRESSED']
    self.pedal_gas = cp.vl["GAS_PEDAL"]['GAS_PEDAL']
    self.car_gas = self.pedal_gas
    self.esp_disabled = cp.vl["ESP_CONTROL"]['TC_DISABLED']

    # calc best v_ego estimate, by averaging two opposite corners
    self.v_wheel_fl = cp.vl["WHEEL_SPEEDS"]['WHEEL_SPEED_FL'] * CV.KPH_TO_MS
    self.v_wheel_fr = cp.vl["WHEEL_SPEEDS"]['WHEEL_SPEED_FR'] * CV.KPH_TO_MS
    self.v_wheel_rl = cp.vl["WHEEL_SPEEDS"]['WHEEL_SPEED_RL'] * CV.KPH_TO_MS
    self.v_wheel_rr = cp.vl["WHEEL_SPEEDS"]['WHEEL_SPEED_RR'] * CV.KPH_TO_MS
    self.v_wheel = float(np.mean([self.v_wheel_fl, self.v_wheel_fr, self.v_wheel_rl, self.v_wheel_rr]))

    # Kalman filter
    if abs(self.v_wheel - self.v_ego) > 2.0:  # Prevent large accelerations when car starts at non zero speed
      self.v_ego_x = np.matrix([[self.v_wheel], [0.0]])

    self.v_ego_raw = self.v_wheel
    v_ego_x = self.v_ego_kf.update(self.v_wheel)
    self.v_ego = float(v_ego_x[0])
    self.a_ego = float(v_ego_x[1])
    self.standstill = not self.v_wheel > 0.001

    self.angle_steers = cp.vl["STEER_ANGLE_SENSOR"]['STEER_ANGLE'] + cp.vl["STEER_ANGLE_SENSOR"]['STEER_FRACTION']
    self.angle_steers_rate = cp.vl["STEER_ANGLE_SENSOR"]['STEER_RATE']
    can_gear = int(cp.vl["GEAR_PACKET"]['GEAR'])
    self.gear_shifter = parse_gear_shifter(can_gear, self.shifter_values)
    self.main_on = cp.vl["PCM_CRUISE_2"]['MAIN_ON']
    self.left_blinker_on = cp.vl["STEERING_LEVERS"]['TURN_SIGNALS'] == 1
    self.right_blinker_on = cp.vl["STEERING_LEVERS"]['TURN_SIGNALS'] == 2

    # 2 is standby, 10 is active. TODO: check that everything else is really a faulty state
    self.steer_state = cp.vl["EPS_STATUS"]['LKA_STATE']
    self.steer_error = cp.vl["EPS_STATUS"]['LKA_STATE'] not in [1, 5]
    self.ipas_active = cp.vl['EPS_STATUS']['IPAS_STATE'] == 3
    self.brake_error = 0
    self.steer_torque_driver = cp.vl["STEER_TORQUE_SENSOR"]['STEER_TORQUE_DRIVER']
    self.steer_torque_motor = cp.vl["STEER_TORQUE_SENSOR"]['STEER_TORQUE_EPS']
    # we could use the override bit from dbc, but it's triggered at too high torque values
    self.steer_override = abs(self.steer_torque_driver) > STEER_THRESHOLD

    self.user_brake = 0
    self.v_cruise_pcm = cp.vl["PCM_CRUISE_2"]['SET_SPEED']
    self.pcm_acc_status = cp.vl["PCM_CRUISE"]['CRUISE_STATE']
    self.gas_pressed = not cp.vl["PCM_CRUISE"]['GAS_RELEASED']
    self.low_speed_lockout = cp.vl["PCM_CRUISE_2"]['LOW_SPEED_LOCKOUT'] == 2
    self.brake_lights = bool(cp.vl["ESP_CONTROL"]['BRAKE_LIGHTS_ACC'] or self.brake_pressed)
    if self.CP.carFingerprint == CAR.PRIUS:
      self.generic_toggle = cp.vl["AUTOPARK_STATUS"]['STATE'] != 0
    else:
      self.generic_toggle = bool(cp.vl["LIGHT_STALK"]['AUTO_HIGH_BEAM'])
