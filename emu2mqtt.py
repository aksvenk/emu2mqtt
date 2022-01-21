#!/usr/bin/env python2

## emu2mqtt
# Export Rainforest Automation EMU-2 energy monitoring data to MQTT

## Attribution
# This script is derived from the excellent [emu2influx](https://github.com/abaker/emu2influx) project by Alex Baker. Credit for the basic flow of the script and EMU API interaction goes to him.
# This script uses the [Emu-Serial-API](https://github.com/rainforestautomation/Emu-Serial-API) by Rainforest Automation.

import logging
import paho.mqtt.client as mqtt
from datetime import datetime
from emu import *
import signal
import sys

mqtt.Client.connected_flag = False
mqtt.Client.bad_connection_flag = False

Y2K = 946684800
int_max = 2**31-1
uint_max = 2**32-1

def get_timestamp(obj):
    if obj.TimeStamp is None:
        obj.TimeStamp = "0x0"
    return datetime.utcfromtimestamp(Y2K + int(obj.TimeStamp, 16)).isoformat()

def get_reading(reading, obj):
    reading = int(reading, 16) * int(obj.Multiplier, 16)
    if reading > int_max:
        reading = -1 * (uint_max - reading)
    return reading / float(int(obj.Divisor, 16))

def get_price(obj):
    return int(obj.Price, 16) / float(10 ** int(obj.TrailingDigits, 16))

def publish_message(mqttc, message):
    logging.info(message)
    publish_msg = mqttc.publish(message["topic"], message["value"], int(args.mqtt_qos), False)
    publish_msg.wait_for_publish()

def on_sigint(sig, frame):
    global exiting
    if not exiting:
        exiting = True
        logging.info("Caught a SIGINT, cleaning up and exiting")
        mqttc.loop_stop()
        mqttc.disconnect()
        emuc.stop_serial()
        time.sleep(4)
        sys.exit()

def on_mqtt_connect(client, userdata, flags, result):
    if result == 0:
        logging.info("Connected to MQTT.")
        client.connected_flag = True
    else:
        logging.critical("Error on MQTT connect: " + str(result))
        client.bad_connection_flag = True

def on_mqtt_disconnect(client, userdata, result):
    if result != 0:
        logging.error("MQTT disconnected, error " + result)
        client.connected_flag = False

def main():
    signal.signal(signal.SIGINT, on_sigint)

    mqttc.on_connect = on_mqtt_connect
    mqttc.on_disconnect = on_mqtt_disconnect
    mqttc.will_set(args.mqtt_topic + "/lwt", "offline", int(args.mqtt_qos), True)
    mqttc.username_pw_set(args.mqtt_username, args.mqtt_password)
    mqttc.connect_async(args.mqtt_server, int(args.mqtt_port), 60)

    emuc.start_serial()
    logging.info("Connected to EMU serial")

    last_demand = 0
    last_price = 0
    last_reading = 0

    lwt_counter = 0

    mqttc.loop_start()
    logging.info("Connecting to MQTT broker " + args.mqtt_server + ":" + str(args.mqtt_port) + " as " + args.mqtt_client_name)

    while True:
        while not mqttc.connected_flag:
            logging.debug("Waiting to connect to MQTT...")
            time.sleep(3)
            if mqttc.bad_connection_flag:
                mqttc.loop_stop()
                sys.exit()

        logging.debug("Sleeping for a second")
        time.sleep(1)
        logging.debug("Checking for serial messages")

        # Publish lwt (last will and testament) every 60 seconds
        if mqttc.connected_flag:
            logging.debug("lwt counter: %d" % lwt_counter)
            lwt_counter = 0 if lwt_counter >= 60 else lwt_counter

            if lwt_counter == 0:
                lwt_msg = mqttc.publish(args.mqtt_topic + "/lwt", "online", int(args.mqtt_qos), True)
                lwt_msg.wait_for_publish()
            
            lwt_counter += 1

        try:
            instantaneous_demand = emuc.InstantaneousDemand
            timestamp = get_timestamp(instantaneous_demand)
            if timestamp > last_demand:
                message = {
                    "topic": args.mqtt_topic + "/demand",
                    "value": get_reading(instantaneous_demand.Demand, instantaneous_demand),
                    "timestamp": timestamp
                }
                publish_message(mqttc, message)
                last_demand = timestamp
        except AttributeError:
            pass
        except TypeError:
            pass

        try:
            current_summation = emuc.CurrentSummationDelivered
            timestamp = get_timestamp(current_summation)
            if timestamp > last_reading:
                imports = get_reading(current_summation.SummationDelivered,
                                         current_summation)
                exports = get_reading(current_summation.SummationReceived,
                                         current_summation)
                value_json = '{ "import": %.3f , "export": %.3f }' % (imports, exports)
                message = {
                    "topic": args.mqtt_topic + "/reading",
                    "value": value_json,
                    "timestamp": timestamp
                }
                publish_message(mqttc, message)
                last_reading = timestamp
        except AttributeError:
            pass
        except TypeError:
            pass

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action='store_true', help="enable debug logging", required=False)
    parser.add_argument("--mqtt_client_name", help="MQTT client name", required=False, default='emu2mqtt')
    parser.add_argument("--mqtt_server", help="MQTT server", required=False, default='localhost')
    parser.add_argument("--mqtt_port", help="MQTT server port", required=False, default=1883)
    parser.add_argument("--mqtt_username", help="MQTT username", required=False, default='')
    parser.add_argument("--mqtt_password", help="MQTT password", required=False, default='')
    parser.add_argument("--mqtt_topic", help="MQTT root topic", required=False, default='emu2mqtt')
    parser.add_argument("--mqtt_qos", help="MQTT QoS", required=False, default=0)
    parser.add_argument("serial_port", help="Rainforest EMU-2 serial port, e.g. 'ttyACM0'")
    return parser.parse_args()

if __name__ == '__main__':
    args = parse_args()
    logging.basicConfig(level=('DEBUG' if args.debug else 'INFO'),
                        format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
    emuc = emu(args.serial_port)
    mqttc = mqtt.Client(args.mqtt_client_name)
    exiting = False
    main()
