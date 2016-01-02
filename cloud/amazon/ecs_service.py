#!/usr/bin/python
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

DOCUMENTATION = '''
---
module: ecs_service
short_description: Ensure a service is present or absent in ecs
description:
    - Creates, updates or deletes Amazon Web Services ECS services.
    - Note: Once a service is created with a load balancer configuration, you cannot change the service load balancer configuration.  If you need to change the load balancer configuration, you must first delete the service and re-create the service.
version_added: "2.1"
author: Justin Menga(@mixja)
requirements: [ boto, boto3 ]
options:
    name: 
        description:
            - The name of the service
        required: True
    state:
        description:
            - The state of the service
        required: True
        choices: ['present', 'absent']
    cluster:
        description:
            - The name of the cluster to run the service on.
        required: False
        default: default
    task_definition:
        description:
            - The task definition family and optional revision of the service in the format family[:revision] or ARN format. Required if state is present.
        required: False
        default: null
    desired_count:
        description:
            - The desired count of service instances. Required if state is present.
        required: False
        default: null
    load_balancer:
        description:
            - The ELB name or ARN to access the service from.  If configured, must be configured with role, container_name and container_port parameters. 
        required: False
        default: null
    container_name:
        description:
            - The task definition container name to access the service from the ELB. If configured, must be configured with role, load_balancer and container_port parameters. 
        required: False
        default: null
    container_port:
        description:
            - The task definition container port to access the service from the ELB. If configured, must be configured with role, load_balancer and container_name parameters. 
        required: False
        default: null
    role:
        description:
            - The IAM role name or ARN that allows ECS to configure the specified load_balancer. If configured, must be configured with load_balancer, container_name and container_port parameters. 
        required: False
        default: null
    max_percent:
        description:
            - Upper limit as percentage of desired_count (rounding down to nearest integer) of number of running service instances that can be running during a deployment.
        required: False
        default: null
    min_healthy_percent:
        description:
            - Lower limit as percentage of desired_count (rounding down to nearest integer) of number of running healthy service instances that must be running during a deployment.
        required: False
        default: null
    wait_until_stable:
        description:
            - When creating or updating a service, wait for service to reach a stable state. This is useful if you need to wait for deployment of the service to complete.
        required: False
        default: no
    wait_until_inactive:
        description:
            - When deleting a service, wait for service to reach an INACTIVE state. When deleting a service, the service will first transition from an ACTIVE state to a DRAINING state, and then to an INACTIVE state when all client connections to the service have closed.
        required: False
        default: yes
extends_documentation_fragment:
    - ec2
'''

EXAMPLES = '''
# Simple example of creating or updating a service without a load balancer
- name: Create service
  ecs_service:
      name: console-sample-app-service
      state: present
      cluster: console-sample-app-static-cluster
      task_definition: console-sample-app-static-taskdef
      desired_count: 1
  register: service_output
# Simple example of creating or updating a service with a load balancer. 
# The role, load_balancer, container_name and container_port must be specified.
# Once created, a service with a load balancer configuration cannot be updated, it must be first deleted and then re-created
- name: Create service with load balancer
  ecs_service:
      name: console-sample-app-service
      state: present
      cluster: console-sample-app-static-cluster
      task_definition: console-sample-app-static-taskdef
      role: ecsServiceRole
      load_balancer: my-elb
      container_name: console-sample-app
      container_port: 8000
      desired_count: 2
      min_healthy_percent: 50
      max_percent: 200
  register: service_output
# Simple example of deleting a service
# The delete operation will change the desired count to 0 before deleting the service
- name: Delete a service
  ecs_service:
      name: console-sample-app-service
      state: absent
      cluster: console-sample-app-static-cluster
# Simple example of deleting a service without waiting for the service to reach an INACTIVE state
- name: Delete a service
  ecs_service:
      name: console-sample-app-service
      state: absent
      cluster: console-sample-app-static-cluster
      wait_until_inactive: false
'''

RETURN = '''
service:
    description: details about the service that was created, updated or deleted
    type: complex
'''
from datetime import datetime

try:
    import json
    import boto
    import botocore
    HAS_BOTO = True
except ImportError:
    HAS_BOTO = False

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

class EcsServiceManager:
    """Handles ECS Services"""

    def __init__(self, module):
        self.module = module

        try:
            region, ec2_url, aws_connect_kwargs = get_aws_connection_info(module, boto3=True)
            if not region:
                module.fail_json(msg="Region must be specified as a parameter, in EC2_REGION or AWS_REGION environment variables or in boto configuration file")
            self.ecs = boto3_conn(module, conn_type='client', resource='ecs', region=region, endpoint=ec2_url, **aws_connect_kwargs)
        except boto.exception.NoAuthHandlerFound, e:
            self.module.fail_json(msg="Can't authorize connection - " + str(e))

    def describe_task_definition(self, task_definition):
        try: 
            response = self.ecs.describe_task_definition(taskDefinition=task_definition)
        except Exception as e:
            self.module.fail_json(msg="Can't describe task definition - " + str(e))
        return response['taskDefinition']

    def describe_services(self, cluster_name, service_name):
        try:
            response = self.ecs.describe_services(
                    cluster=cluster_name,
                    services=[service_name]
                )
        except Exception as e:
            self.module.fail_json(msg="Can't describe service - " + str(e))
        if response['services']:
            return response['services'][0]
        return None

    def create_service(self, wait_until_stable, cluster_name, service_name, desired_count, task_definition, load_balancer=None, container_name=None, container_port=None, role=None, min_healthy_percent=None, max_percent=None):
        """Creates a service"""
        args = dict()
        deployment_config = dict()
        load_balancers = dict()
        args['cluster'] = cluster_name
        args['serviceName'] = service_name
        args['desiredCount'] = desired_count
        args['taskDefinition'] = task_definition
        if role:
            args['role'] = role
        if load_balancer:
            load_balancers['loadBalancerName'] = load_balancer
        if container_name:
            load_balancers['containerName'] = container_name
        if container_port:
            load_balancers['containerPort'] = container_port
        if min_healthy_percent:
            deployment_config['minimumHealthyPercent'] = min_healthy_percent
        if max_percent:
            deployment_config['maximumPercent'] = max_percent
        if deployment_config:
            args['deploymentConfiguration'] = deployment_config
        if load_balancers:
            args['loadBalancers'] = [load_balancers]
        try:
            response = self.ecs.create_service(**args)
            if wait_until_stable:
                self.wait_until_stable(cluster_name, service_name)
                response['service'] = self.describe_services(cluster_name, service_name)
        except Exception as e:
            self.module.fail_json(msg="Can't create service - " + str(e))
        return response['service']

    def update_service(self, wait_until_stable, cluster_name, service_name, desired_count, task_definition=None, min_healthy_percent=None, max_percent=None):
        """Updates an existing service"""
        args = dict()
        deployment_config = dict()
        args['cluster'] = cluster_name
        args['service'] = service_name
        args['desiredCount'] = desired_count
        if task_definition:
            args['taskDefinition'] = task_definition
        if min_healthy_percent:
            deployment_config['minimumHealthyPercent'] = min_healthy_percent
        if max_percent:
            deployment_config['maximumPercent'] = max_percent
        if deployment_config:
            args['deploymentConfiguration'] = deployment_config
        try:
            response = self.ecs.update_service(**args)
            if wait_until_stable:
                self.wait_until_stable(cluster_name, service_name)
                response['service'] = self.describe_services(cluster_name, service_name)
        except Exception as e:
            self.module.fail_json(msg="Can't update service - " + str(e))
        return response['service']

    def delete_service(self, cluster_name, service_name, wait):
        """Deletes a service"""
        try:
            # Set service desired count to zero
            response = self.update_service(False, cluster_name, service_name, 0)

            # Delete service
            self.ecs.delete_service(cluster=cluster_name, service=service_name)

            if wait:
                # Wait for service to become inactive
                waiter = self.ecs.get_waiter('services_stable')
                waiter.wait(cluster=cluster_name, services=[ service_name ])
            
            response = self.describe_services(cluster_name, service_name)
        except Exception as e:
            self.module.fail_json(msg="Can't delete service - " + str(e))
        return response

    def wait_until_stable(self, cluster_name, service_name):
        """Waits for service to become stable"""
        waiter = self.ecs.get_waiter('services_stable')
        waiter.wait(cluster=cluster_name, services=[ service_name ])

    def check_for_update(self, desired, existing, task_definition_arn):
        """Compares desired state with existing state to determine if an update is required"""
        # Construct target state
        target=dict()
        target_deployment_config=dict()
        existing_deployment_config=existing.get('deploymentConfiguration')
        target['taskDefinition'] = task_definition_arn
        target['desiredCount'] = desired.get('desired_count')
        if desired['min_healthy_percent']:
            target_deployment_config['minimumHealthyPercent'] = desired.get('min_healthy_percent')
        if desired['max_percent']:
            target_deployment_config['maximumPercent'] = desired.get('max_percent')
        return [item for item in target.items() if item not in existing.items()] or \
               [item for item in target_deployment_config.items() if item not in existing_deployment_config.items()]

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime.datetime):
        serial = obj.isoformat()
        return serial
    raise TypeError ("Type not serializable")

def fix_datetime(result):
    """Temporary fix to convert datetime fields from Boto3 to datetime string.  See https://github.com/ansible/ansible-modules-extras/issues/1348."""
    return json.loads(json.dumps(result, default=json_serial))

def main():
    argument_spec = ec2_argument_spec()
    argument_spec.update(dict(
        name=dict(required=True, type='str'),
        state=dict(required=True, choices=['present', 'absent']),
        cluster=dict(default='default', required=False, type='str'),
        task_definition=dict(required=False, type='str' ), 
        load_balancer=dict(required=False, type='str'),
        container_name=dict(required=False, type='str'),
        container_port=dict(required=False, type='int'),
        role=dict(required=False, type='str'),
        desired_count=dict(required=False, type='int'),
        min_healthy_percent=dict(required=False, type='int'),
        max_percent=dict(required=False, type='int'),
        wait_until_stable=dict(default=False, required=False, choices=BOOLEANS),
        wait_until_inactive=dict(default=True, required=False, choices=BOOLEANS)
    ))

    module = AnsibleModule(argument_spec=argument_spec, supports_check_mode=True)

    # Validate Requirements
    if not HAS_BOTO:
      module.fail_json(msg='boto is required.')

    if not HAS_BOTO3:
      module.fail_json(msg='boto3 is required.')

    # Validate Inputs
    if module.params['state'] == 'present':
        if module.params['desired_count'] is None:
            module.fail_json(msg="To ensure the service is present, the desired_count must be specified")
        if module.params['task_definition'] is None:
            module.fail_json(msg="To ensure the service is present, a task_definition must be specified")
        if not ((module.params['load_balancer'] is None) == (module.params['role'] is None) == \
               (module.params['container_port'] is None) == (module.params['container_name'] is None)):
            module.fail_json(msg="When configuring load_balancer, container_name, container_port or role - you must specify load_balancer, container_name, container_port and role")

    # Get existing service
    service_mgr = EcsServiceManager(module)
    existing = service_mgr.describe_services(module.params['cluster'], module.params['name'])
    results = dict(changed=False)

    if module.params['state'] == 'absent' and existing and existing.get('status') == 'ACTIVE':    
        if not module.check_mode:
            results['service'] = fix_datetime(service_mgr.delete_service(
                module.params['cluster'],
                module.params['name'],
                module.params['wait_until_inactive']
            ))
        results['changed'] = True
    elif module.params['state'] == 'present':
        # Get task definition
        task_definition = service_mgr.describe_task_definition(module.params['task_definition'])
        if task_definition['status'] != 'ACTIVE':
            module.fail_json(msg="You must specify an active task definition")
        task_definition_arn = task_definition['taskDefinitionArn']

        if not existing or existing.get('status') != 'ACTIVE' :
            if not module.check_mode:
                results['service'] = fix_datetime(service_mgr.create_service(
                    module.params['wait_until_stable'],
                    module.params['cluster'],
                    module.params['name'],
                    module.params['desired_count'],
                    module.params['task_definition'],
                    module.params['load_balancer'],
                    module.params['container_name'],
                    module.params['container_port'],
                    module.params['role'],
                    module.params['min_healthy_percent'],
                    module.params['max_percent'] 
                ))
            results['changed'] = True

        elif service_mgr.check_for_update(module.params, existing, task_definition_arn):
            if not module.check_mode:
                results['service'] = fix_datetime(service_mgr.update_service(
                    module.params['wait_until_stable'],
                    module.params['cluster'],  
                    module.params['name'],
                    module.params['desired_count'],  
                    module.params['task_definition'],
                    module.params['min_healthy_percent'],
                    module.params['max_percent'] 
                ))
            results['changed'] = True

        else:
            results['service'] = fix_datetime(existing)

    module.exit_json(**results)

# import module snippets
from ansible.module_utils.basic import *
from ansible.module_utils.ec2 import *

if __name__ == '__main__':
    main()