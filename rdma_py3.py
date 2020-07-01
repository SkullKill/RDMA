#!/usr/bin/python3

#########################################################################################
# 
# rpi-temp-snmp-alarm (RTSA)
# 
# Written by Simon Kong for the Raspberry Pi
# V1.0 14/05/2019
# 
# dynamicly adjust to the number of DS18B20 temperature sensor added to the config file
# it will automaticly generate the snmp files, and add the relevant config lines into snmpd
# all snmp will be under .1.3.6.1.4.1.8072.2.X.X e.g .1.3.6.1.4.1.8072.2.100.1
# all settings in the DEFAULT section can be copied to individual sections to customise only that sensor/relay
# status and running config can also be accessed via a web interface

import signal
import configparser
import os
import time
import RPi.GPIO as GPIO
import datetime
#import http.server
#from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO
import urllib
import threading

# libraries for display
import board
import busio
from adafruit_ht16k33 import segments


config = configparser.ConfigParser()
# Temperature sensor list
temp_list = []
# Relay list
relay_list = []
output_list = []
# input setup has not been done yet
inputsetup = False
# input list
input_list = []
# phone number to use to send sms
sms_list = []
sms_sent = False

# temp va;ies
temp_values = dict()

# relay alarm state
relay_alarm_state = dict()
# mute state
mute_state = dict()
# timer state
timer_state = dict()

# handle kill signal
def handle_exit(sig, frame):
    raise(SystemExit)
# Handle kill signal
def setup_OSsignal():
    signal.signal(signal.SIGTERM, handle_exit)

# read config file
def read_config():

    config.read('/etc/rdma.ini')
    #config.read('rdma.ini')

    return



def reset_relay_state():
    for relay_id in relay_list:
        toogleValue = config.getboolean(relay_id, 'state')
        relay_alarm_state[relay_id]['alarm'] = False
        if not GPIO.input(config.getint(relay_id, 'gpio')) == toogleValue:
            GPIO.output(config.getint(relay_id, 'gpio'), toogleValue)
            relay_alarm_state[relay_id]['state'] = toogleValue
            print("{} - setting relay {} to OFF Becase Mute {} ".format(datetime.datetime.now(), relay_id, toogleValue) )
            print(temp_values)
    return

def setup_timer():
    # initialize timer_state
    timer_state['state'] = False
    timer_state['normal'] = config.getfloat('system', 'timer_normal', fallback="300")
    timer_state['extended'] = config.getfloat('system', 'timer_extended', fallback="900")
    timer_state['extended_start'] = datetime.datetime.now()
    timer_state['extended_mode'] = False
    timer_state['timer'] = timer_state['normal']
    timer_state['lastreset'] = datetime.datetime.now()
    #print("timer state start {}".format(timer_state['extended_start']))
    return

def setup_mute():
    #initiallize mute_state
    mute_state['state'] = False
    mute_state['date'] = (datetime.datetime.now() - datetime.timedelta(days=-1))
    mute_state['mode'] = config.get('mute_button', 'mute_mode', fallback="momentary")

    return

# turn output on (opposite to that initial state in config is)
def output_on(output_id):
    toogleValue = True
    if config.getboolean(output_id, 'state') == True :
        toogleValue = False
    GPIO.output(config.getint(output_id, 'gpio'), toogleValue)
    #output_alarm_state[output_id]['state'] = toogleValue
    #print("{} - setting output {} to ON {} ".format(datetime.datetime.now(), output_id, toogleValue) )
    #print(temp_values)
    return

# turn relay off (same to waht state in config)
def output_off(output_id):
    toogleValue = config.getboolean(output_id, 'state')
    GPIO.output(config.getint(output_id, 'gpio'), toogleValue)
    #output_alarm_state[output_id]['state'] = toogleValue
    #print("{} - setting output {} to OFF {} ".format(datetime.datetime.now(), output_id, toogleValue) )
    #print(temp_values)
    return


# small status buzzer, turn on for length in sec, and repeat for times
def beep(length, times):
    if not config.getboolean('202', 'enable'):
        return
    #pin = config.getint('202', 'gpio')
    pin = str(202)
    for i in range(times):
        output_on(pin)
        time.sleep(length)
        output_off(pin)
        time.sleep(length)
    return

# call the buzzer function in a non blocking way (in a thread)
def beep_noblock(length, times):
    threading.Thread(target=beep, args=(length, times)).start()
    return


def enable_motionExtendedMode():
    print("{} Enable ExtendedMode".format(datetime.datetime.now()))
    beep_noblock(config.getfloat('202', 'alt_length'), config.getint('202', 'alt_times'))
    timer_state['extended_start'] = datetime.datetime.now()
    timer_state['timer'] = timer_state['extended']
    timer_state['lastreset'] = datetime.datetime.now()
    timer_state['extended_mode'] = True
    return

def disable_motionExtendedMode():
    print("{} Disable EntendedMode".format(datetime.datetime.now()))
    #timer_state['lastreset'] = datetime.datetime.now()
    timer_state['timer'] = timer_state['normal']
    timer_state['extended_mode'] = False
    return

def reset_MotionSensor():
    #print("{} Reset MotionSensor timer".format(datetime.datetime.now()))
    timer_state['lastreset'] = datetime.datetime.now()
    global sms_sent
    sms_sent = False
    return

def reset_MotionSensor_Manual():
    reset_MotionSensor()
    disable_motionExtendedMode()
    return

def MotionSensor(pin):
    #print("{} Motion Sensor triggered".format(datetime.datetime.now()))
    reset_MotionSensor()
    return

def ResetButton(pin):
    print("{} Reset Button triggered".format(datetime.datetime.now()))
    beep_noblock(config.getfloat('202', 'button_length'), config.getint('202', 'button_times'))
    alternateFunction = False
    time.sleep(config.getfloat('308', 'interference_debounce', fallback=0.1))
    if GPIO.input(pin) == config.getboolean('308', 'state', fallback=1):
        print("interference detected, skipping")
    #handleInputChange(pin)
    else:
        timePressed = datetime.datetime.now()
        # activate solenoid relay to unlock door
        while not alternateFunction:
            if (datetime.datetime.now() > (timePressed + datetime.timedelta(seconds=config.getfloat('308','alt_holdoff', fallback=5)))):
                alternateFunction = True
            if GPIO.input(pin) == config.getboolean('308', 'state'):
                break
        if alternateFunction:
            enable_motionExtendedMode()
        else:
            reset_MotionSensor_Manual()
    return

def send_sms():
    global sms_sent
    if not (sms_sent):
        sms_sent = True
        first_run = True
        beep_noblock(config.getfloat('202', 'sms_length'), config.getint('202', 'sms_times'))
        #url = 'curl --include --header "Authorization: Basic {}" --request POST --header "Content-Type: application/json" --data-binary "    {{ \\"messages\\":['.format(config.get('clicksend', 'api'))
        url = 'curl --user {}:{} --include --request POST --header "Content-Type: application/json" --data-binary "    {{ \\"messages\\":['.format(config.get('clicksend', 'username'), config.get('clicksend', 'api'))
        for sms_id in sms_list:
            if (config.getboolean(sms_id, 'enable', fallback=1)):
                if not (first_run):
                    url = url + (',')
                first_run = False
                sms_name = config.get(sms_id, 'name', fallback="name")
                sms_phone = config.get(sms_id, 'phone')
                print("{} Sending SMS to {} phone {}".format(datetime.datetime.now(), sms_name, sms_phone))
                #curl
                url = url + ('{{ \\"source\\":\\"php\\", \\"body\\":\\"Hi {}, {}. {}\\", \\"to\\":\\"{}\\" }}'.format(sms_name, config.get('clicksend', 'message'),datetime.datetime.now(), sms_phone))
        url = url + ('] }" \'https://rest.clicksend.com/v3/sms/send\'')
        #print(url)
        if (config.getboolean('clicksend', 'enable')):
            print("actually exec {}".format(url))
            # comment out to disable sms 
            os.system(url)
    return

def send_sms_noblock():
    threading.Thread(target=send_sms ).start()
    return

# when the mute butt
def mute(pin):
    print("{} Mute button Pressed".format(datetime.datetime.now()))
    mute_state['date'] = datetime.datetime.now()
    if mute_state['mode'] == "momentary":
        mute_state['state'] = True
        output_on('205')
        print("{} Mute enabled".format(datetime.datetime.now()))
        reset_relay_state()
    elif mute_state['mode'] == "toogle":
        if mute_state['state']:
            mute_state['state'] = False
            output_off('205')
            print("{} Mute cleared".format(datetime.datetime.now()))
        else:
            mute_state['state'] = True
            output_on('205')
            print("{} Mute enabled".format(datetime.datetime.now()))
            reset_relay_state()

    return

def process_mute():
    if mute_state['mode'] == "momentary":
    #print("Processing Mute")
        if (mute_state['date'] + datetime.timedelta(minutes=config.getfloat('mute_button', 'timer', fallback=15))) < (datetime.datetime.now()):
            print("{} Mute cleared".format(datetime.datetime.now()))
            mute_state['state'] = False
            output_off('205')
    return

def setup_input():
    #setup mute variables
    setup_mute()
    setup_timer()
    for input_id in input_list:
        # pull down PUD_DOWN 1
        pull_up_or_down = GPIO.PUD_DOWN
        # Rising Edge
        edge = GPIO.RISING
        gpio = config.getint(input_id, 'gpio')
        state = config.getboolean(input_id, 'state', fallback=1)
        if (state) == True:
            # pull up PUD_UP 2
            #print("state is true")
            pull_up_or_down = GPIO.PUD_UP
        #setup input pin, and setup pull_up_down resistor mode
        GPIO.setup(gpio, GPIO.IN, pull_up_down=pull_up_or_down)

        # if PUD_UP 2, then monitor for falling edge
        if (pull_up_or_down == GPIO.PUD_UP) and not (config.getboolean(input_id, 'nc', fallback=0)):
            # Falling Edge
            edge = GPIO.FALLING
            #print("input {} GPIO.FALLING".format(input_id))
        # initialize the interrupt
        if (config.getboolean(input_id, 'actionboth', fallback=0)):
            edge = GPIO.BOTH
            #print("input {} GPIO.BOTH".format(input_id))

        #print("input {} edge = {}".format(input_id, edge))
        #print("setting up gpio interupt")
        #callbackfn = getattr(config.get(input_id, 'name'))
        #GPIO.add_event_detect(gpio, edge, callback=config.get(input_id, 'name'), bouncetime=300)
        #GPIO.add_event_detect(gpio, edge, (locals()[config.get(input_id, 'name')]()), bouncetime=300)
        GPIO.add_event_detect(gpio, edge, (globals()[config.get(input_id, 'name')]), bouncetime=config.getint(input_id, 'debounce_time', fallback=300))
        #GPIO.add_event_detect(gpio, edge, (globals()[config.get(input_id, 'name')]), bouncetime=600)
        #print("initiallizing input_values")
        #ActualState = GPIO.input(gpio)
        #input_values[input_id] = state
        #if config.getboolean(input_id, 'link_status'):
        #    handleInputChange(gpio, ActualState)

    #if (config.getboolean('mute_button', 'enable')):
    #    gpio = config.getint('mute_button', 'gpio', fallback=13)
    #    if (config.getboolean('mute_button', 'state', fallback=True)) == True:
    #        # pull up PUD_UP 2
    #        pull_up_or_down = GPIO.PUD_UP
    #    #setup input pin, and setup pull_up_down resistor mode
    #    GPIO.setup(gpio, GPIO.IN, pull_up_down=pull_up_or_down)

    #    # if PUD_UP 2, then monitor for falling edge
    #    if pull_up_or_down == GPIO.PUD_UP:
    #        # Falling Edge
    #        edge = GPIO.FALLING
    #    # initialize the interrupt
    #    GPIO.add_event_detect(gpio, edge, callback=mute, bouncetime=300)
    # finish all the input setup
    global inputsetup
    inputsetup = True
    
    return

def setup_GPIO():
    GPIO.setmode(GPIO.BCM) # Broadcom pin-numbering scheme
    #GPIO.setwarnings(False)

    # setup all relay (output) gpio
    for relay in relay_list:
        GPIO.setup(config.getint(relay, 'gpio'), GPIO.OUT) # set as output
        GPIO.output(config.getint(relay, 'gpio'), config.getboolean(relay, 'state', fallback=False)) 
    
    # setup all output (output) gpio
    for output in output_list:
        #print("setting gpio {}".format(config.getint(output, 'gpio')))
        GPIO.setup(config.getint(output, 'gpio'), GPIO.OUT) # set as output
        GPIO.output(config.getint(output, 'gpio'), config.getboolean(relay, 'state', fallback=False)) 
    
    # setup all input gpio
    #for inputtype in input_list:
    #    GPIO.setup(config.getint(inputtype, 'gpio'), GPIO.IN) # set as output
    setup_input()

    return

def setup_display():
    # please check that POSOTIONS has been changed in 
    # /usr/local/lib/python3.5/dist-packages/adafruit_ht16k33/segments.py
    # to be like
    # POSITIONS = (0, 2, 4, 6)  #  The positions of characters.
    #
    global display

    # Create the I2C interface.
    i2c = busio.I2C(board.SCL, board.SDA)
     
    # Create the LED segment class.
    # This creates a 7 segment 4 character display:
    display = segments.Seg7x4(i2c)
      
    # Clear the display.
    display.fill(0)
    return

def process_config():
    # Check each section for sensors / relay
    for key in config.sections():
        # check if it is meant to be enable
        if config.getboolean(key, 'enable', fallback=True) == True:
            # check if there is a type sub config in this section
            if config.has_option(key, 'type'):
                if config[key]['type'] == 'temp':
                    temp_list.append(key)
                elif config[key]['type'] == 'relay':
                    relay_list.append(key)
                elif config[key]['type'] == 'output':
                    output_list.append(key)
                elif config[key]['type'] == 'input':
                    input_list.append(key)
                elif config[key]['type'] == 'smsnumber':
                    sms_list.append(key)
    return

def create_temp_values_files():
    # if dir dpes not exist, create it
    if not os.path.exists(config['system']['temp_values_folder']):
        os.makedirs(config['system']['temp_values_folder'])

    # check that all temp values files exist, and create it if is does not exist
    for sensor_id in temp_list:
        file_name = (config['system']['temp_values_folder'] + config[sensor_id]['name'])
        # remove if exist (to recreate)
        #if os.path.exists(file_name):
        #    os.remove(file_name)
        if not os.path.exists(file_name):
            os.mknod(file_name, mode=0o644)
            print(file_name)

    # create rdma_timer file
    file_name = (config['system']['temp_values_folder'] + "rdma_timer")
    if not os.path.exists(file_name):
        os.mknod(file_name, mode=0o644)
        print(file_name)
    
    return

def create_snmp_custom_files():
    # if dir dpes not exist, create it
    if not os.path.exists(config['system']['snmp_folder']):
        os.makedirs(config['system']['snmp_folder'])

    # check that all snmp files exist, and create it if is does not exist
    for sensor_id in temp_list:
        file_name = (config['system']['snmp_folder'] + "snmp-" + config[sensor_id]['name'] + ".sh")
        # remove if exist (to recreate)
        #if os.path.exists(file_name):
        #    os.remove(file_name)
        if not os.path.exists(file_name):
            os.mknod(file_name, mode=0o755)
            print(file_name)

            lines = [
                    "#!/bin/bash\n", 
                    "if [ \"$1\" = \"-g\" ]\n", 
                    "then\n", 
                    "   echo .1.3.6.1.4.1.8072.2." + config.get('system', 'baseID_temp') + "." + str(int(sensor_id) - config.getint('system', 'baseID_temp')) + "\n", 
                    "   echo string\n",
                    "   temp=\"cat " + (config['system']['temp_values_folder'] + config[sensor_id]['name']) + "\"\n",
                    "   eval \"$temp\"\n",
                    "   echo \"\\n\"\n",
                    "fi\n",
                    "exit 0\n"]
            print(lines)
            f = open(file_name, 'w+')
            f.writelines(lines)
            f.close()
            
            # Add in snmpd config file
            f2 = open(config.get('system', 'snmp_config_file'), 'a+')
            f2.write("pass .1.3.6.1.4.1.8072.2." + config.get('system', 'baseID_temp') + "." + str(int(sensor_id) - config.getint('system', 'baseID_temp')) + "   /bin/sh " + config['system']['snmp_folder'] + "snmp-" + config[sensor_id]['name'] + ".sh" + "\n")
            f2.close()

    # create rdma_timer file.
    file_name = (config['system']['snmp_folder'] + "snmp-" + "rdma_timer" + ".sh")
    # remove if exist (to recreate)
    #if os.path.exists(file_name):
    #    os.remove(file_name)
    if not os.path.exists(file_name):
        os.mknod(file_name, mode=0o755)
        print(file_name)

        lines = [
                "#!/bin/bash\n", 
                "if [ \"$1\" = \"-g\" ]\n", 
                "then\n", 
                "   echo .1.3.6.1.4.1.8072.2." + config.get('system', 'baseID_dma') + "." + "1" + "\n", 
                "   echo string\n",
                "   temp=\"cat " + config.get('system', 'temp_values_folder') + "rdma_timer" + "\"\n",
                "   eval \"$temp\"\n",
                "   echo \"\\n\"\n",
                "fi\n",
                "exit 0\n"]
        print(lines)
        f = open(file_name, 'w+')
        f.writelines(lines)
        f.close()
            
        # Add in snmpd config file
        f2 = open(config.get('system', 'snmp_config_file'), 'a+')
        f2.write("pass .1.3.6.1.4.1.8072.2." + config.get('system', 'baseID_dma') + "." + "1" + "   /bin/sh " + config['system']['snmp_folder'] + "snmp-" + "rdma_timer" + ".sh" + "\n")
        f2.close()

    return

# Read in the data from the Temp Sensor file
def read_1_wire_temp_raw(sensor_id):
    f = open(config[sensor_id]['file'], 'r')
    lines = f.readlines()
    f.close()
    
    return lines

# Process the Temp Sensor file for errors and convert to degrees C
def read_1_wire_temp(sensor_id):
    lines = read_1_wire_temp_raw(sensor_id)
    while lines[0].strip()[-3:] != 'YES':
        sleep(0.2)
        lines = read_1_wire_temp_raw(sensor_id)
    
    equals_pos = lines[1].find('t=')

    if equals_pos != -1:
        temp_string = lines[1][equals_pos + 2:]
        # Use line below for Celsius
        temp_curr = float(temp_string) / 1000.0
        #Uncomment line below for Fahrenheit
        #temp_curr = ((float(temp_string) / 1000.0) * (9.0 / 5.0)) + 32
    
    return temp_curr

def toogle_sensor_alerting(sensor_id):
    if temp_values[sensor_id]['sensor_alerting']:
        print("{} - Disabling Alerting for sensor_ID {} ".format(datetime.datetime.now(), sensor_id) )
        temp_values[sensor_id]['sensor_alerting'] = False
        reset_relay_state()
    else:
        print("{} - Enabling Alerting for sensor_ID {} ".format(datetime.datetime.now(), sensor_id) )
        temp_values[sensor_id]['sensor_alerting'] = True

# read all sonsors and store values in temp values folder
def read_sensors():
    for sensor_id in temp_list:
        # delay before query sensor
        time.sleep(config.getfloat(sensor_id, 'delay_before', fallback=0))
        temp_temp = read_1_wire_temp(sensor_id)
        # delay after query sensor
        time.sleep(config.getfloat(sensor_id, 'delay_after', fallback=1))
        #print("sensorid {} is {}".format(sensor_id, temp_temp))
        #offset correction (if any)
        temp_temp = temp_temp + config.getfloat(sensor_id, 'sensor_offset', fallback=0)
        #print("sensorid {} is {} after offset".format(sensor_id, temp_temp))
        temp_values[sensor_id]['temp'] = temp_temp

        # Store it in the temp values folder
        file_name = (config['system']['temp_values_folder'] + config[sensor_id]['name'])
        f = open(file_name, 'w+')
        f.write(str(temp_temp) + "\n")
        f.close()
        #print(temp_values)
    return

def initiallize_sensor_dic():
    for sensor_id in temp_list:
        temp_values[sensor_id] = dict()
        temp_values[sensor_id]['sensor_alerting'] = config.getboolean(sensor_id, 'sensor_alerting', fallback=1)
    return

def initiallize_relay_dic():
    for relay_id in relay_list:
        # only change after times, if opposite, will toogle physical switch
        #relay_alarm_state[relay_id] = config.getboolean(relay_id, 'state') 
        relay_alarm_state[relay_id] = dict()
        relay_alarm_state[relay_id]['state'] = config.getboolean(relay_id, 'state') 
        # instanatly change is any sensors are in alarm state
        relay_alarm_state[relay_id]['alarm'] = False
        relay_alarm_state[relay_id]['date'] = (datetime.datetime.now() - datetime.timedelta(days=-1))
        relay_alarm_state[relay_id]['momentary_date'] = (datetime.datetime.now() - datetime.timedelta(days=-1))
        relay_alarm_state[relay_id]['momentary_first'] = True
        #for sensor_id in temp_list:
        #    relay_alarm_state[relay_id][sensor_id] = dict()
        #    relay_alarm_state[relay_id][sensor_id]['alarm'] = False
        #    relay_alarm_state[relay_id][sensor_id]['date'] = (datetime.datetime.now() - datetime.timedelta(days=-1))
    #print(relay_alarm_state)

    return

# turn relay on (opposite to that initial state in config is)
def relay_on(relay_id):
    toogleValue = True
    if config.getboolean(relay_id, 'state') == True :
        toogleValue = False
    GPIO.output(config.getint(relay_id, 'gpio'), toogleValue)
    relay_alarm_state[relay_id]['state'] = toogleValue
    #print("{} - setting relay {} to ON {} ".format(datetime.datetime.now(), relay_id, toogleValue) )
    #print(temp_values)
    return

# turn relay off (same to waht state in config)
def relay_off(relay_id):
    toogleValue = config.getboolean(relay_id, 'state')
    GPIO.output(config.getint(relay_id, 'gpio'), toogleValue)
    relay_alarm_state[relay_id]['state'] = toogleValue
    #print("{} - setting relay {} to OFF {} ".format(datetime.datetime.now(), relay_id, toogleValue) )
    #print(temp_values)
    return

def momentary_relay_procedure(relay_id):
    #print("Doing Momentary relay procedure")
    relay_on(relay_id)
    time.sleep(config.getfloat(relay_id, 'momentary_relay_timer', fallback=1))
    relay_off(relay_id)
    relay_alarm_state[relay_id]['momentary_date'] = (datetime.datetime.now())


def process_relays():
    if mute_state['state'] == True:
        process_mute()
        return

    for relay_id in relay_list:
        alarm = False
        for sensor_id in temp_list:
            # if this sensor is not support to be doing alerting, skip it.
            if not temp_values[sensor_id]['sensor_alerting']:
                continue
            if (temp_values[sensor_id]['temp']) < (config.getfloat(sensor_id, 'lower_alert_value', fallback=-23) + config.getfloat(relay_id, 'alarm_range_low_offset', fallback=0)):
                #relay_alarm_state[relay_id][sensor_id]['alarm'] = True
                #relay_alarm_state[relay_id][sensor_id]['date'] = (datetime.datetime.now())
                alarm = True
            elif (temp_values[sensor_id]['temp']) > (config.getfloat(sensor_id, 'upper_alert_value', fallback=5) + config.getfloat(relay_id, 'alarm_range_high_offset', fallback=0)):
                #relay_alarm_state[relay_id][sensor_id]['alarm'] = True
                #relay_alarm_state[relay_id][sensor_id]['date'] = (datetime.datetime.now())
                alarm = True
        #    else:
                #relay_alarm_state[relay_id][sensor_id]['alarm'] = False
        # if any of the sensors are in alarm state, set the relay alarm state to True
        #for sensor_id in temp_list
        #    if relay_alarm_state[relay_id][sensor_id]['alarm'] = True
        #        alarm = True

        #        if relay_alarm_state[relay_id]['alarm'] = False
        #            relay_alarm_state[relay_id]['alarm'] = True
        #            relay_alarm_state[relay_id]['date'] = (datetime.datetime.now())
        #if (datetime.datetime.now() > (timer_state['lastreset'] + datetime.timedelta(seconds=timer_state['timer']))):
        if (datetime.datetime.now() > ((timer_state['lastreset'] + datetime.timedelta(seconds=timer_state['timer'])) + datetime.timedelta(seconds=config.getfloat(relay_id, 'delay_relay_on', fallback=-1)))):
            alarm = True
            # trigger sms alarm

        if alarm == True:
            if relay_alarm_state[relay_id]['alarm'] == False :
                relay_alarm_state[relay_id]['alarm'] = True
                relay_alarm_state[relay_id]['date'] = (datetime.datetime.now())
                relay_alarm_state[relay_id]['momentary_date'] = (datetime.datetime.now())
                relay_alarm_state[relay_id]['momentary_first'] = True
        else:
            if relay_alarm_state[relay_id]['alarm'] == True :
                relay_alarm_state[relay_id]['alarm'] = False
                relay_alarm_state[relay_id]['date'] = (datetime.datetime.now())

        # if no alarm state (relay on) AND alarm trigger is yes (need to trun on)
        if ((config.getboolean(relay_id, 'state') == relay_alarm_state[relay_id]['state']) and relay_alarm_state[relay_id]['alarm'] == True ):
            # if timer expired toogle relay to on
            if (datetime.datetime.now() > (relay_alarm_state[relay_id]['date'] + datetime.timedelta(seconds=config.getfloat(relay_id, 'delay_relay_on', fallback=1)))):
                # if relay is in momentary mode (door bell)
                if (config.get(relay_id, 'relay_mode', fallback="toogle") == "momentary" ):
                    # if it is the first time running the momentary procedure, do it instantly
                    if (relay_alarm_state[relay_id]['momentary_first']):
                        momentary_relay_procedure(relay_id)
                        relay_alarm_state[relay_id]['momentary_first'] = False
                    # if not the first time, then check that timer has expired before doing momentary procedure
                    elif (datetime.datetime.now() > (relay_alarm_state[relay_id]['momentary_date'] + datetime.timedelta(seconds=config.getfloat(relay_id, 'momentary_relay_interval', fallback=5)))):
                        momentary_relay_procedure(relay_id)
                # if not in momentary mode, therefore toogle mode, just thrn relay on
                else:
                    relay_on(relay_id)
        # if alarm state (relay off) AND alarm trigger is no (need to turn off)
        elif ((config.getboolean(relay_id, 'state') != relay_alarm_state[relay_id]['state']) and relay_alarm_state[relay_id]['alarm'] == False ):
            # if timer expired toogle relay to off
            if (datetime.datetime.now() > (relay_alarm_state[relay_id]['date'] + datetime.timedelta(seconds=config.getfloat(relay_id, 'delay_relay_off', fallback=1)))):
                relay_off(relay_id)
    
    return

def process_motion():

    if (timer_state['extended_mode']) and (datetime.datetime.now() > (timer_state['extended_start'] + datetime.timedelta(seconds=timer_state['extended']))):
        disable_motionExtendedMode()
    #timeleft = ((timer_state['lastreset'] + datetime.timedelta(seconds=timer_state['timer'])) - datetime.datetime.now()).total_seconds()
    timeleft = str(((timer_state['lastreset'] + datetime.timedelta(seconds=timer_state['timer'])) - datetime.datetime.now()).total_seconds()).split('.')[0]
    timeover = (datetime.datetime.now() - (timer_state['lastreset'] + datetime.timedelta(seconds=timer_state['timer'])))
    if (datetime.datetime.now() > (timer_state['lastreset'] + datetime.timedelta(seconds=timer_state['timer']))):
        # send sms
        #print("{} Alarm , no Motion detected {}".format(datetime.datetime.now(), timeover))
        print("{} Alarm , no Motion detected {}".format(datetime.datetime.now(), timeleft))
        send_sms_noblock()
    else:
        print("{} Motion timeout within range {}".format(datetime.datetime.now(), timeleft))

    # Store it in the temp values folder
    file_name = config.get('system', 'temp_values_folder') + "rdma_timer"
    f = open(file_name, 'w+')
    f.write(timeleft + "\n")
    f.close()


    return

def process_display_noblock():
    #threading.Thread(target=process_display_loop ).start()
    tdisplay = threading.Thread(target=process_display_loop )
    tdisplay.setDaemon(True)
    tdisplay.start()
    return

def process_display_loop():
    while True:
        process_display()
        time.sleep(1)

def process_display():
    #timeleft2 = str(((timer_state['lastreset'] + datetime.timedelta(seconds=timer_state['timer'])) - datetime.datetime.now()).total_seconds()).split('.')[0]
    #timeleft.strftime("%I.%<")
    minsec = ""
    if (datetime.datetime.now() > (timer_state['lastreset'] + datetime.timedelta(seconds=timer_state['timer']))):
        timeover = str((datetime.datetime.now() - (timer_state['lastreset'] + datetime.timedelta(seconds=timer_state['timer'])))).split('.')[0]
        hour,minutes,seconds = timeover.split(':')
        minutes = minutes[1:]
        minsec = "-" + minutes + "." + seconds
        #print("display time over timeover: {} minsec: {}".format(timeover, minsec))

    else:
        timeleft = str(((timer_state['lastreset'] + datetime.timedelta(seconds=timer_state['timer'])) - datetime.datetime.now())).split('.')[0]
        hour,minutes,seconds = timeleft.split(':')
        minsec = minutes + "." + seconds
        #print("display time left timeleft: {} minsec: {}".format(timeleft, minsec))

    display.print(minsec)

    return


class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def _set_headers(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

    def do_HEAD(self):
        self._set_headers()
    def do_GET(self):
        if self.path == '/README.md':
            self.path = 'README.md'
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"""<html><head><title>RDMA - Rpi Dead Man Alarm temp snmp</title>
                <meta http-equiv="refresh" content="10">
                <style>
                body {
                    height: 100%;
                    background-repeat: repeat-x;
                    background-image: -webkit-gradient(linear, top, bottom, color-stop(0, #0060BF), color-stop(1, #5CC3FF));
                    background-image: -o-linear-gradient(top, #0060BF, #5CC3FF);
                    background-image: -moz-linear-gradient(top, #0060BF, #5CC3FF);
                    background-image: -webkit-linear-gradient(top, #0060BF, #5CC3FF);
                    background-image: linear-gradient(to bottom, #0060BF, #5CC3FF);
                    background-attachment: fixed;
                }
                table {
                    font-family: arial, sans-serif;
                    border-collapse: collapse;
                    width: 100%;
                }

                td, th {
                    border: 1px solid #dddddd;
                    text-align: left;
                    padding: 8px;
                }

                tr:nth-child(even) {
                    background-color: #dddddd;
                }
                </style>
                </head><body>""")
        # last refresh
        now = "last refresh was at : {}".format(datetime.datetime.now())
        self.wfile.write(bytes(now, 'utf-8'))
        # Motion Timer
        timeleft = ((timer_state['lastreset'] + datetime.timedelta(seconds=timer_state['timer'])) - datetime.datetime.now()).total_seconds()
        if (datetime.datetime.now() > (timer_state['lastreset'] + datetime.timedelta(seconds=timer_state['timer']))):
            motion_message = '<p style="background-color:#ff5050;color:#000000;font-weight:bold">Motion in Alarm state, <br>Time Over = {} seconds</p>'.format(timeleft)
        else:
            motion_message = '<p style="background-color:#50ff50;color:#000000;font-weight:bold">Motion not Alarming, <br>Time Left = {} seconds</p>'.format(timeleft)
        self.wfile.write(bytes(motion_message, 'utf-8'))
        self.wfile.write(b"<br>")

        #mute status
        mute_status = ""
        if mute_state['mode'] == "momentary":
            if mute_state['state']:
                timediff = (mute_state['date'] + datetime.timedelta(minutes=config.getfloat('mute_button', 'timer', fallback=10))) - (datetime.datetime.now())
                timediff = str(timediff).split('.', 1)[0]
                mute_status = """<p style="background-color:#ff5050;color:#000000;font-weight:bold">mute status is momentary and ENABLED, <br>We will NOT be alerting!!!!!! 
                    <br>Mute status will turn off in {} hours:minutes:seconds</p>""".format(timediff)
            else:
                mute_status = '<p style="background-color:#50ff50;color:#000000;font-weight:bold">mute status is momentary and disabled, <br>Alerting is working, all good</p>'
        elif mute_state['mode'] == "toogle":
            if mute_state['state']:
                mute_status = '<p style="background-color:#ff5050;color:#000000;font-weight:bold">mute status is toogle and ENABLED, <br>We will NOT be alerting!!!!!!<br>Mute status will NOT turn off until manually turned off</p>'
            else:
                mute_status = '<p style="background-color:#50ff50;color:#000000;font-weight:bold">mute status is toogle and disabled, <br>Alerting is working, all good</p>'
        self.wfile.write(bytes(mute_status, 'utf-8'))
        self.wfile.write(b"<br>")
        # mute post form
        #self.wfile.write(b"<form action='.' method='POST'><label for='mute'>MUTE: </label><input name='mute' value='ALL' /><input type='submit' /></form>")
        mute_form = "<form action='.' method='POST'><label for='mute'>Option: </label><select name='mute'><option value='RESET_TIMER'>Reset Timer - restart timer</option>"
        mute_form = mute_form + "<option value='EXTENDED'>Extend - Enable Extended Timer Mode</option>"
        mute_form = mute_form + "<option value='ALL'>ALL - mute whole system</option>"
        for sensor_id in temp_list:
            sensor_name = config.get(sensor_id, 'name')
            mute_form = mute_form + "<option value='{}'>{} - {}</option>".format(sensor_id, sensor_id, sensor_name)
        mute_form = mute_form + "</select><input type='submit' /></form>"
        self.wfile.write(bytes(mute_form, 'utf-8'))
        self.wfile.write(b"chose 'ALL' to mute the whole system, or the sensor id, e.g '101' to disable alerting for that sensor<br>")
        self.wfile.write(b"<br>")

        # temp status table
        self.wfile.write(b"<h2>Temperature Status Table</h2>")
        self.wfile.write(b"""<table>
                <tr>
                    <th>ID</th>
                    <th>Name</th>
                    <th>Temperature</th>
                    <th>Status</th>
                    <th>Alerting</th>
                </tr>""")
        tempStatus = ""
        for sensor_id in temp_values:
            tempStatus = tempStatus + """
            <tr>
                <td>{}</td>
                <td>{}</td>
                <td>{} &#8451</td>
            """.format(sensor_id, config.get(sensor_id, 'name'), temp_values[sensor_id]['temp'])
            # if temp higher
            if (temp_values[sensor_id]['temp']) > (config.getfloat(sensor_id, 'upper_alert_value', fallback=5)):
                tempStatus = tempStatus + '<td style="background-color:#ff5050;color:#ffffff;font-weight:bold">Too HOT!!!</td>'
            # if temp lower
            elif (temp_values[sensor_id]['temp']) < (config.getfloat(sensor_id, 'lower_alert_value', fallback=-23)):
                tempStatus = tempStatus + '<td style="background-color:#5050ff;color:#ffffff;font-weight:bold">Too COLD!!!</td>'
            # if within range
            else:
                tempStatus = tempStatus + '<td style="background-color:#50ff50;color:#ffffff;font-weight:bold">OK</td>'
            # if alerting is turn on, all good
            if temp_values[sensor_id]['sensor_alerting']:
                tempStatus = tempStatus + '<td style="background-color:#50ff50;color:#ffffff;font-weight:bold">Enabled</td>'
            else:
                tempStatus = tempStatus + '<td style="background-color:#ff5050;color:#ffffff;font-weight:bold">Alerting is OFF!!!</td>'

            tempStatus = tempStatus + "</tr>"
        self.wfile.write(bytes(tempStatus, 'utf-8'))
        self.wfile.write(b"</table>")
        self.wfile.write(b"</body></html>")
        #self.wfile.write(b'Hello, world!')

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        body = self.rfile.read(content_length)
        self.send_response(200)
        self.end_headers()
        response = BytesIO()
        response.write(b"""<html><head><title>RDMA - Rpi Dead Man Alarm temp snmp</title>
                <meta http-equiv="refresh" content="2">
                <style>
                body {
                    height: 100%;
                    background-repeat: repeat-x;
                    background-image: -webkit-gradient(linear, top, bottom, color-stop(0, #0060BF), color-stop(1, #5CC3F
F));
                    background-image: -o-linear-gradient(top, #0060BF, #5CC3FF);
                    background-image: -moz-linear-gradient(top, #0060BF, #5CC3FF);
                    background-image: -webkit-linear-gradient(top, #0060BF, #5CC3FF);
                    background-image: linear-gradient(to bottom, #0060BF, #5CC3FF);
                    background-attachment: fixed;
                }
                </style>
                </head><body>""")
        response.write(b'This is POST request. ')
        response.write(b'Received: ')
        #response.write(body)
        #response.write(b'<br>')
        #test()
        print("post data is {}".format(body))
        postdata = body.decode('utf-8')
        print("post data is {}".format(postdata))
        try:
            postdisc = dict(item.split("=") for item in postdata.split("&"))
            #print("postdisc is {}".format(postdisc))
            if 'mute' in postdisc:
                #print("mute is present")
                postdisc['mute'] = urllib.parse.unquote(postdisc['mute'])
                #print("unquote done")
                if postdisc['mute'] == "ALL":
                    mute(13)
                    print("Mute button from WEB")
                    response.write(b'Mute button pressed! ')
                elif postdisc['mute'] == "RESET_TIMER":
                    #reset_MotionSensor()
                    reset_MotionSensor_Manual()
                elif postdisc['mute'] == "EXTENDED":
                    enable_motionExtendedMode()
                else:
                    for sensor_id in temp_list:
                        if postdisc['mute'] == sensor_id:
                            #toogle mute for that sensor
                            toogle_sensor_alerting(sensor_id)
                            response.write(b'Toogle Alerting ')
                            break
            else:
                response.write(b'no valid post ')
                print("web: no valid post")
        except:
            response.write(b'wrong post format ')
            print("web: wrong post format")
        response.write(b'</body></html>')
        self.wfile.write(response.getvalue())


def httpd_server():
    print("starting httpd server")
    httpd = HTTPServer((config.get('system', 'httpd_address', fallback="localhost"), config.getint('system', 'httpd_port', fallback=80)), SimpleHTTPRequestHandler)
    httpd.serve_forever()
    return

def start_httpd_server():
    #threading.Thread(target=httpd_server).start()
    thttpd = threading.Thread(target=httpd_server)
    thttpd.setDaemon(True)
    thttpd.start()
    return


################
#              #
# Main Program #
#              #
################

print("\n\n{} - starting Rpi Dead Man Alarm monitor".format(datetime.datetime.now()))
setup_OSsignal()
read_config()

# wait a bit before starting the program
time.sleep(config.getfloat('system', 'delay_startup', fallback=10))



process_config()
create_temp_values_files()
create_snmp_custom_files()
initiallize_relay_dic()
initiallize_sensor_dic()

# DISABLE setup mute if setup_GPIO is on
#setup_mute()
#####################################
#setup_GPIO()
#start_httpd_server()
#output_on('205')
#while True:
#    read_sensors()
#    process_relays()
#    process_motion()
#    time.sleep(config.getfloat('system', 'delay_cycle', fallback=50))

try:
    # setup the GPIO pins
    setup_GPIO()
    setup_display()
    start_httpd_server()
    process_display_noblock()
    # turn on status light
    output_on('205')
    # main loop
    while True:
        # uncomment the next line, to log when the next cycle is starthing
        #print("{} - Starting new cycle".format(datetime.datetime.now()))
        
        read_sensors()
        # uncomment the next line, to log the recorded temperature
        #print(temp_values)
        
        process_relays()
        process_motion()
        #process_display()
        #send_sms_noblock()
        time.sleep(config.getfloat('system', 'delay_cycle', fallback=50))

except KeyboardInterrupt:
    print("Keyboard Inturrupt detected")

except SystemExit:
    print("kill signal detected")

except:
    print("Some other error detected")

finally:
    # eigher way, do this before exit
    # Clear the display.
    display.fill(0)
    print("{} - cleanning up GPIO pins".format(datetime.datetime.now()))
    GPIO.cleanup()

#####################################


print("\n=============Debug Stuff==================")
temp_list_amount=len(temp_list)
relay_list_amount=len(relay_list)


print(datetime.datetime.now())
print(datetime.timedelta(days=-1))
print(datetime.datetime.now() - datetime.timedelta(days=-1))
print("number of temp sensors : {}".format(temp_list_amount))
print(temp_list)
print("number of relay sensors : {}".format(relay_list_amount))
print(relay_list)
print(output_list)

for sensor_id, temp in temp_values.items():
    print(sensor_id, temp)

temp_values["101"] = 50

print(temp_values)

print(relay_alarm_state)



