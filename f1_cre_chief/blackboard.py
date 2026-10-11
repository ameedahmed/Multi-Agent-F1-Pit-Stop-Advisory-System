"""
Blackboard is a shared race-state where all specialized 
agents write/read events using append-only method.
"""

import json
import redis
from typing import Dict, Any, List, Optional

class RaceBlackboard:
    def __init__(self,host:str = "localhost",port: int = 6379, db: int=0):
        #Connect to redis environment
        #Decode responses converts redis bytes into python strings
        self.r = redis.Redis(host=host,port=port,db=db,decode_responses=True)

    #Storing real-time key-value pairs for a driver's metrics by updating them    
    def update_driver_state(self,driver_number:str,metrics:Dict[str,Any])->None:
        #Below code writes to a redis hash data structure. Key is named as driver-number & metrics involve all telemetry in a network request
        self.r.hset(f"driver:{driver_number}:state",mapping=metrics)
    
    def read_events_after(self, last_id: str = "0-0", count: int = 10,
                          block_ms: int = 2000, stream_key: str = "race_events") -> List[Dict[str, Any]]:
        # xread returns entries NEWER than last_id. block makes Redis wait up to block_ms for new
        # entries instead of us busy-looping. It returns None on timeout, hence the `or []`.
        resp = self.r.xread({stream_key: last_id}, count=count, block=block_ms)
        parsed = []
        for _, entries in resp or []:
            for event_id, data in entries:
                parsed.append({"event_id": event_id, "type": data.get("type"),
                               "payload": json.loads(data.get("payload", "{}"))})
        return parsed
    
    #Read driver's current state from memory
    def get_driver_state(self,driver_number:str)->Dict[str,str]:
        #Retrieve all key-value pairs for that driver's state (redis hash)
        return self.r.hgetall(f"driver:{driver_number}:state")
    
    #Append new data to the redis stream
    def publish_event(self,event_type:str,payload:Dict[str,Any])->str:
        #Make a dictionary containing event-name & convert the payload dict into a JSON string
        event = {"type":event_type,"payload":json.dumps(payload)}
        #Append the event to a redis stream called race_events with an auto-generated event-ID
        event_id = self.r.xadd("race_events",event)
        return event_id
    
    #Read latest events in reverse chronological order
    def read_latest_events(self,stream_key:str="race_events",count:int=5)->List[Dict[str,Any]]:
        #Read the Redis Stream in reverse order to fetch the count of most recent items
        events = self.r.xrevrange(stream_key,count=count)
        parsed_events = []
        for event_id, data in events:
            parsed_events.append({
                "event_id":event_id,
                "type":data.get("type"),
                #Json.loads converts string payload into a Python Dictionary
                "payload":json.loads(data.get("payload","{}"))
            })
        return parsed_events