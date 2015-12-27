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
short_description: create, update or delete a service in ecs
description:
    - Creates, updates or deletes ecs services.
version_added: "2.1"
author: Justin Menga(@mixja)
requirements: [ boto, boto3 ]
options:
    name: 
        description:
            - The name of the service
        required: True
    operation:
        description:
            - Which service operation to execute
        required: True
        choices: ['create', 'update', 'delete']
    cluster:
        description:
            - The name of the cluster to run the service on.
        required: False
        default: default
    task_definition:
        description:
            - The task definition family and optional revision of the service in the format family[:revision] or ARN format. Required to create a new service.
        required: False
        default: null
    load_balancer:
        description:
            - The ELB name or ARN to access the service from.  If configured, must be configured with role, container_name and container_port parameters.
        required: False
        default: null
    container_name:
        description:
            - The task definition container name to access the service from the ELB.  If configured, must be configured with role, load_balancer and container_port parameters.
        required: False
        default: null
    container_port:
        description:
            - The task definition container port to access the service from the ELB.  If configured, must be configured with role, load_balancer and container_name parameters.
        required: False
        default: null
    role:
        description:
            - The IAM role name or ARN that allows ECS to configure the specified load_balancer.  If configured, must be configured with load_balancer, container_name and container_port parameters.
        required: False
        default: null
    desired_count:
        description:
            - The desired count of service instances.  Required for creating a service.
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
    wait_until_inactive:
        description:
            - When deleting a service, wait for service to reach an INACTIVE state.  When deleting a service, the service will first transition from an ACTIVE state to a DRAINING state, and then to an INACTIVE state when all client connections to the service have closed.
        required: False
        default: yes
extends_documentation_fragment:
    - ec2
'''

EXAMPLES = '''
# Simple example of create service without a load balancer
- name: Create service
  ecs_service:
    name: console-sample-app-service
    operation: create
    cluster: console-sample-app-static-cluster
    task_definition: console-sample-app-static-taskdef
    desired_count: 1
  register: service_output
# Simple example of create service with a load balancer. 
# The role, load_balancer, container_name and container_port must be specified.
- name: Create service with load balancer
  ecs_service:
      name: console-sample-app-service
      operation: create
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
# Simple example of updating a service
- name: Update a service to use latest task definition revision
  ecs_service:
      name: console-sample-app-service
      operation: update
      cluster: console-sample-app-static-cluster
      task_definition: console-sample-app-static-taskdef
# Simple example of updating a service to a specific task definition revision
- name: Update a service to use specific task definition revision
  ecs_service:
      name: console-sample-app-service
      operation: update
      cluster: console-sample-app-static-cluster
      task_definition: console-sample-app-static-taskdef:5
# Simple example of changing the number of service instances
- name: Update a service to use four instances
  ecs_service:
      name: console-sample-app-service
      operation: update
      cluster: console-sample-app-static-cluster
      desired_count: 4
# Simple example of deleting a service
# The delete operation will change the desired count to 0 before deleting the service
- name: Delete a service
  ecs_service:
      name: console-sample-app-service
      operation: delete
      cluster: console-sample-app-static-cluster
# Simple example of deleting a service without waiting for the service to reach an INACTIVE state
- name: Delete a service
  ecs_service:
      name: console-sample-app-service
      operation: delete
      cluster: console-sample-app-static-cluster
      wait_until_inactive: false
'''

RETURN = '''
service:
    description: details about the service that was created, updated or deleted
    type: complex
    sample: "TODO: include sample"
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
            self.module.fail_json(msg="Can't authorize connection - "+str(e))

    def describe_services(self, cluster_name, service_name):
        try:
            response = self.ecs.describe_services(
                    cluster=cluster_name,
                    services=[service_name]
                )
        except Exception as e:
            self.module.fail_json(msg="Can't describe services - "+str(e))
        if response['services']:
            return response['services'][0]
        return None


    def create_service(self, service_name, desired_count, task_definition, cluster_name='default', load_balancer=None, container_name=None, container_port=None, role=None, min_healthy_percent=None, max_percent=None):
        args = dict()
        deployment_config = dict()
        load_balancers = dict()
        args['serviceName'] = service_name
        args['taskDefinition'] = task_definition
        args['desiredCount'] = desired_count
        args['cluster'] = cluster_name
        if load_balancer:
            load_balancers['loadBalancerName'] = load_balancer
        if container_name:
            load_balancers['containerName'] = container_name
        if container_port:
            load_balancers['containerPort'] = container_port
        if role:
            args['role'] = role
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
        except Exception as e:
            self.module.fail_json(msg="Can't create service - "+str(e))
        return response['service']

    def update_service(self, service_name, desired_count=-1, cluster_name='default', task_definition=None, min_healthy_percent=None, max_percent=None):
        args = dict()
        deployment_config = dict()
        args['service'] = service_name
        args['cluster'] = cluster_name
        if task_definition:
            args['taskDefinition'] = task_definition
        if desired_count >= 0:
            args['desiredCount'] = desired_count
        if min_healthy_percent:
            deployment_config['minimumHealthyPercent'] = min_healthy_percent
        if max_percent:
            deployment_config['maximumPercent'] = max_percent
        if deployment_config:
            args['deploymentConfiguration'] = deployment_config
        try:
            response = self.ecs.update_service(**args)
        except Exception as e:
            self.module.fail_json(msg="Can't update service - "+str(e))
        return response['service']

    def delete_service(self, wait, service_name, cluster_name='default'):
        try:
            # Set service desired count to zero
            response = self.update_service(service_name, 0, cluster_name)

            # Delete service
            self.ecs.delete_service(cluster=cluster_name, service=service_name)

            if wait:
                # Wait for service to become inactive
                waiter = self.ecs.get_waiter('services_inactive')
                waiter.wait(cluster=cluster_name, services=[ service_name ])
            
            response = self.describe_services(cluster_name, service_name)
        except Exception as e:
            self.module.fail_json(msg="Can't delete service - "+str(e))
        return response

    def check_for_update(self, desired, existing):
        """Compares desired state with existing state to determine any changes required"""
        # Construct target state
        target=dict()
        target_deployment_config=dict()
        existing_deployment_config=existing.get('deploymentConfiguration')
        if desired.get('desired_count'):
            target['desiredCount'] = desired.get('desired_count')
        if desired.get('task_definition'):
            target['taskDefinition'] = desired.get('task_definition')
        if desired.get('min_healthy_percent'):
            target_deployment_config['minimumHealthyPercent'] = desired.get('min_healthy_percent')
        if desired.get('max_percent'):
            target_deployment_config['maximumPercent'] = desired.get('max_percent')
        return [item for item in target.items() if item not in existing.items()] or \
               [item for item in target_deployment_config.items() if item not in existing_deployment_config.items()]

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        serial = obj.isoformat()
        return serial
    raise TypeError ("Type not serializable")

def fix_datetime(result):
    """Temporary fix to convert datetime fields from Boto3 to dateiime string.  See https://github.com/ansible/ansible-modules-extras/issues/1348."""
    return json.loads(json.dumps(result, default=json_serial))

def main():
    argument_spec = ec2_argument_spec()
    argument_spec.update(dict(
        name=dict(required=True, type='str'),
        operation=dict(required=True, choices=['create', 'update', 'delete']),
        cluster=dict(required=False, type='str'),
        task_definition=dict(required=False, type='str' ), 
        load_balancer=dict(required=False, type='str'),
        container_name=dict(required=False, type='str'),
        container_port=dict(required=False, type='int'),
        role=dict(required=False, type='str'),
        desired_count=dict(required=False, type='int'),
        min_healthy_percent=dict(required=False, type='int'),
        max_percent=dict(required=False, type='int'),
        wait_until_inactive=dict(default=True, required=False, choices=BOOLEANS)
    ))

    module = AnsibleModule(argument_spec=argument_spec, supports_check_mode=True)

    # Validate Requirements
    if not HAS_BOTO:
      module.fail_json(msg='boto is required.')

    if not HAS_BOTO3:
      module.fail_json(msg='boto3 is required.')

    # Validate Inputs
    if module.params['operation'] == 'create':
        if not 'desired_count' in module.params and module.params['desired_count'] is None:
            module.fail_json(msg="To create a service, the desired_count must be specified")
        if not 'task_definition' in module.params and module.params['task_definition'] is None:
            module.fail_json(msg="To create a service, a task_definition must be specified")
        if not ((module.params['load_balancer'] is None) == (module.params['role'] is None) == \
               (module.params['container_port'] is None) == (module.params['container_name'] is None)):
            module.fail_json(msg="When configuring load_balancer, container_name, container_port or role - you must specify load_balancer, container_name, container_port and role")

    # Get existing service
    service_mgr = EcsServiceManager(module)
    existing = service_mgr.describe_services(module.params['cluster'], module.params['name'])
    results = dict(changed=False)

    if module.params['operation'] == 'create':
        if existing and existing.get('status') == 'ACTIVE':
            results['service'] = fix_datetime(existing)
        else:
            if not module.check_mode:
                results['service'] = fix_datetime(service_mgr.create_service(
                    module.params.get('name'),
                    module.params.get('desired_count'),    
                    module.params.get('task_definition'),
                    module.params.get('cluster'),
                    module.params.get('load_balancer'),
                    module.params.get('container_name'),
                    module.params.get('container_port'),
                    module.params.get('role'),
                    module.params.get('min_healthy_percent'),
                    module.params.get('max_percent') 
                ))
            results['changed'] = True

    elif module.params['operation'] == 'update':
        if not existing:
            module.fail_json(msg="Service to update was not found")
        elif service_mgr.check_for_update(module.params, existing):
            if not module.check_mode:
                results['service'] = fix_datetime(service_mgr.update_service(
                    module.params.get('name'),
                    module.params.get('desired_count'),  
                    module.params.get('cluster'),  
                    module.params.get('task_definition'),
                    module.params.get('min_healthy_percent'),
                    module.params.get('max_percent') 
                ))
            results['changed'] = True

    elif module.params['operation'] == 'delete':
        if not existing:
            module.fail_json(msg="Service to delete was not found")
        else:
            if not module.check_mode:
            # it exists, so we should delete it and mark changed.
            # return info about the cluster deleted
                results['service'] = fix_datetime(service_mgr.delete_service(
                    module.params['wait_until_inactive'],
                    module.params['name'],
                    module.params['cluster']
                ))
            results['changed'] = True

    module.exit_json(**results)

# import module snippets
from ansible.module_utils.basic import *
from ansible.module_utils.ec2 import *

if __name__ == '__main__':
    main()