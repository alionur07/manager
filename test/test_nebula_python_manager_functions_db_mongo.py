from unittest import TestCase
from functions.db.mongo import *


def mongo_connection():
    mongo_url = os.getenv("MONGO_URL", "mongodb://nebula:nebula@127.0.0.1:27017/nebula?authSource=admin")
    connection = MongoConnection(mongo_url)
    return connection


def create_temp_app(mongo_connection_object, app):
    app_conf = {
        "starting_ports": [80],
        "containers_per": {"server": 1},
        "env_vars": {"TEST": "test123"},
        "docker_image": "nginx"
    }
    reply = mongo_connection_object.mongo_add_app(app, app_conf["starting_ports"],  app_conf["containers_per"],
                                                  app_conf["env_vars"], app_conf["docker_image"])
    return reply


class MongoTests(TestCase):

    def test_mongo_app_flow(self):
        mongo_connection_object = mongo_connection()

        # ensure no test app is already created in the unit test DB
        mongo_connection_object.mongo_remove_app("unit_test_app")

        # check create test app works
        test_reply = create_temp_app(mongo_connection_object, "unit_test_app")
        self.assertEqual(test_reply["app_id"], 1)
        self.assertEqual(test_reply["app_name"], "unit_test_app")
        self.assertEqual(test_reply["containers_per"], {"server": 1})
        self.assertEqual(test_reply["devices"], [])
        self.assertEqual(test_reply["docker_image"], "nginx")
        self.assertEqual(test_reply["env_vars"], {"TEST": "test123"})
        self.assertEqual(test_reply["networks"], "nebula")
        self.assertFalse(test_reply["privileged"])
        self.assertFalse(test_reply["rolling_restart"])
        self.assertTrue(test_reply["running"])
        self.assertEqual(test_reply["volumes"], [])

        # check getting test app data works
        app_exists, test_reply = mongo_connection_object.mongo_get_app("unit_test_app")
        self.assertTrue(app_exists)
        self.assertEqual(test_reply["app_id"], 1)
        self.assertEqual(test_reply["app_name"], "unit_test_app")
        self.assertEqual(test_reply["containers_per"], {"server": 1})
        self.assertEqual(test_reply["devices"], [])
        self.assertEqual(test_reply["docker_image"], "nginx")
        self.assertEqual(test_reply["env_vars"], {"TEST": "test123"})
        self.assertEqual(test_reply["networks"], "nebula")
        self.assertFalse(test_reply["privileged"])
        self.assertFalse(test_reply["rolling_restart"])
        self.assertTrue(test_reply["running"])
        self.assertEqual(test_reply["volumes"], [])

        # check getting test app data non existing app
        app_exists, test_reply = mongo_connection_object.mongo_get_app("unit_test_app_that_doesnt_exist")
        self.assertFalse(app_exists)

        # check if app exists works
        test_reply = mongo_connection_object.mongo_check_app_exists("unit_test_app")
        self.assertTrue(test_reply)
        test_reply = mongo_connection_object.mongo_check_app_exists("unit_test_app_that_doesnt_exist")
        self.assertFalse(test_reply)

        # check getting app envvars works
        test_reply = mongo_connection_object.mongo_list_app_envvars("unit_test_app")
        self.assertEqual(test_reply, {"TEST": "test123"})

        # check updating app envvars works
        test_reply = mongo_connection_object.mongo_update_app_envars("unit_test_app", {"NEW_TEST": "new_test123"})
        self.assertEqual(test_reply["env_vars"], {"NEW_TEST": "new_test123"})

        # check updating app somefield works
        test_reply = mongo_connection_object.mongo_update_app_fields("unit_test_app", {
            "env_vars":
                {"TESTING": "testing123"},
            "running": False
        })
        self.assertEqual(test_reply["env_vars"], {"TESTING": "testing123"})
        self.assertFalse(test_reply["running"])

        # check getting app number of containers per cpu works
        test_reply = mongo_connection_object.mongo_list_app_containers_per("unit_test_app")
        self.assertEqual(test_reply, {"server": 1})

        # check updating app number of containers per cpu works
        test_reply = mongo_connection_object.mongo_update_app_containers_per("unit_test_app", {"server": 2})
        self.assertEqual(test_reply["containers_per"], {"server": 2})

        # check getting app starting ports works
        test_reply = mongo_connection_object.mongo_list_app_starting_ports("unit_test_app")
        self.assertEqual(test_reply, [80])

        # check updating app starting ports works
        test_reply = mongo_connection_object.mongo_update_app_starting_ports("unit_test_app", {"81": "80"})
        self.assertEqual(test_reply["starting_ports"], {"81": "80"})

        # check increase app id works
        test_reply = mongo_connection_object.mongo_increase_app_id("unit_test_app")
        test_app_id = test_reply["app_id"]
        test_reply = mongo_connection_object.mongo_increase_app_id("unit_test_app")
        self.assertEqual(test_reply["app_id"], test_app_id + 1)

        # check getting app running state works
        test_reply = mongo_connection_object.mongo_list_app_running_state("unit_test_app")
        self.assertFalse(test_reply)

        # check updating app running state works
        test_reply = mongo_connection_object.mongo_update_app_running_state("unit_test_app", True)
        self.assertTrue(test_reply)

        # check getting list of apps works
        test_reply = mongo_connection_object.mongo_list_apps()
        self.assertEqual(test_reply, ["unit_test_app"])

        # check update test app works
        test_reply = mongo_connection_object.mongo_increase_app_id("unit_test_app")
        test_app_id = test_reply["app_id"]
        updated_app_conf = {
            "starting_ports": [80],
            "containers_per": {"server": 1},
            "env_vars": {"TEST": "test123"},
            "docker_image": "nginx",
            "running": True,
            "networks": ["nebula"],
            "volumes": [],
            "devices": [],
            "privileged": True,
            "rolling_restart": True
        }
        test_reply = mongo_connection_object.mongo_update_app("unit_test_app", updated_app_conf["starting_ports"],
                                                              updated_app_conf["containers_per"],
                                                              updated_app_conf["env_vars"],
                                                              updated_app_conf["docker_image"],
                                                              updated_app_conf["running"],
                                                              updated_app_conf["networks"],
                                                              updated_app_conf["volumes"],
                                                              updated_app_conf["devices"],
                                                              updated_app_conf["privileged"],
                                                              updated_app_conf["rolling_restart"])
        self.assertEqual(test_reply["app_id"], test_app_id + 1)
        self.assertEqual(test_reply["app_name"], "unit_test_app")
        self.assertEqual(test_reply["containers_per"], {"server": 1})
        self.assertEqual(test_reply["devices"], [])
        self.assertEqual(test_reply["docker_image"], "nginx")
        self.assertEqual(test_reply["env_vars"], {"TEST": "test123"})
        self.assertEqual(test_reply["networks"], ["nebula"])
        self.assertTrue(test_reply["privileged"])
        self.assertTrue(test_reply["rolling_restart"])
        self.assertTrue(test_reply["running"])
        self.assertEqual(test_reply["volumes"], [])

        # check delete test app works
        test_reply = mongo_connection_object.mongo_remove_app("unit_test_app")
        self.assertEqual(test_reply.deleted_count, 1)

    def test_mongo_device_group_flow(self):
        mongo_connection_object = mongo_connection()

        # ensure no test app is already created in the unit test DB
        mongo_connection_object.mongo_remove_device_group("unit_test_device_group")

        # check create device group works
        test_reply = mongo_connection_object.mongo_add_device_group("unit_test_device_group", [])
        self.assertEqual(test_reply["apps"], [])
        self.assertEqual(test_reply["device_group"], "unit_test_device_group")
        self.assertEqual(test_reply["device_group_id"], 1)
        self.assertEqual(test_reply["prune_id"], 1)

        # check list device group works
        device_group_exists, test_reply = mongo_connection_object.mongo_get_device_group("unit_test_device_group")
        self.assertTrue(device_group_exists)
        self.assertEqual(test_reply["apps"], [])
        self.assertEqual(test_reply["device_group"], "unit_test_device_group")
        self.assertEqual(test_reply["device_group_id"], 1)
        self.assertEqual(test_reply["prune_id"], 1)

        # check list device groups works
        test_reply = mongo_connection_object.mongo_list_device_groups()
        self.assertEqual(test_reply, ["unit_test_device_group"])

        # check update device group works
        test_reply = mongo_connection_object.mongo_update_device_group("unit_test_device_group", [])
        self.assertEqual(test_reply["device_group"], "unit_test_device_group")
        self.assertEqual(test_reply["device_group_id"], 2)
        self.assertEqual(test_reply["prune_id"], 1)

        # check device group exists works
        test_reply = mongo_connection_object.mongo_check_device_group_exists("unit_test_device_group")
        self.assertTrue(test_reply)

        # check increase prune id works
        test_reply = mongo_connection_object.mongo_increase_prune_id("unit_test_device_group")
        test_prune_id = test_reply["prune_id"]
        test_reply = mongo_connection_object.mongo_increase_prune_id("unit_test_device_group")
        self.assertEqual(test_reply["prune_id"], test_prune_id + 1)

        # check delete device group works
        test_reply = mongo_connection_object.mongo_remove_device_group("unit_test_device_group")
        self.assertEqual(test_reply.deleted_count, 1)