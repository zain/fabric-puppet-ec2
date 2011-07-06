import boto.ec2, boto.exception, itertools, os
from fabric.api import *
from fabric.decorators import *

import puppet
from utils import *

### The name of the key pair that will be put into puppet instances.
### https://console.aws.amazon.com/ec2/home#s=KeyPairs
env.ec2_key_pair_name = "puppet"

### ...and the path to the corresponding private key on your machine.
### Alternatively, you can pass this in on the command line with fab -i.
env.key_filename = os.path.expanduser("~/.ssh/ec2-puppet.pem")

### The name of the security group that will be put into puppet instances.
### This group has to allow SSH access on tcp/22 or else fabric can't connect to it.
### This group also needs a security exception for tcp/8140 for all boxen in the group
###  so the puppetmaster can connect to the slaves w/o needing to sign anything.
### https://console.aws.amazon.com/ec2/home#s=SecurityGroups
env.ec2_security_group = "puppet"

### The EC2 region we're focusing on. If your AWS web console isn't showing you anything,
### make sure you've picked this EC2 region in the drop-down on the top-left of the console.
env.ec2_region = "us-west-1"

### The AMIs to use. Here's a couple examples.
### [us-west-1] Ubuntu 11.04 Natty Narwhal 64-bit (via http://alestic.com/):
### - ami-136f3c56: EBS boot; persistent root on EBS volume, possibly slightly slower disk I/O
### - ami-0b6f3c4e: Instance-store; volatile, tied to instance, maybe slightly faster disk I/O

# Puppet Masters:
env.master_ec2_ami = "ami-136f3c56" # ubuntu 11.04 64bit EBS boot
env.master_ec2_instance_type = "t1.micro"

# Puppet Slaves:
env.slave_ec2_ami = "ami-136f3c56" # ubuntu 11.04 64bit EBS boot
env.slave_ec2_instance_type = "m1.large"


@task
def create_master():
    """ Spin up a new EC2 instance and set it up as a puppet master.
    Make sure AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are set in your env.
    """
    require('master_ec2_ami', 'master_ec2_instance_type')

    btw("Spinning up a new puppet master server...")
    inst = ec2_launch(env.master_ec2_ami, env.master_ec2_instance_type)
    inst.add_tag("puppet:type", "puppetmaster")

    env.host_string = "ubuntu@%s" % inst.ip_address
    wait_until_alive()
    puppet.install_master()

    yay("\nSuccess! Puppet master is now alive.\n")

    env.working_master = inst


@task
def create_slaves(num=1, master=None):
    """Spins up n puppet slaves and adds them to the specified puppet master.
        The master can be specified via the "master" parameter (instance ID or puppet:name),
        by setting env.working_master, or interactively if nothing is passed in.
    """
    require('slave_ec2_ami', 'slave_ec2_instance_type')

    master = get_puppetmaster(master)

    btw('Creating %s slaves under master "%s" (%s)...' % (
        num, master.tags.get('puppet:name', '???'), master.id))

    slave_name = prompt("What should we call this/these slave(s)?: ",
        default="%s-slave" % master.tags['puppet:name'])

    env.working_slaves = []

    for i in range(1,int(num)+1):
        btw("\nSlave #%s coming up." % i)
        inst = ec2_launch(env.slave_ec2_ami, env.slave_ec2_instance_type, name=slave_name)
        inst.add_tag("puppet:type", "puppetslave")
        inst.add_tag("puppet:master_id", master.id)

        env.host_string = "ubuntu@%s" % inst.ip_address
        wait_until_alive()
        puppet.install_slave()

        env.working_slaves.append(inst)

    yay("\nSuccess! %s slaves are up.\n" % len(env.working_slaves))


@runs_once
def ec2_connect():
    """Returns a boto connection object, required to do pretty much anything with EC2"""
    require('ec2_region')

    try:
        conn = boto.ec2.connect_to_region(env.ec2_region)
    except boto.exception.NoAuthHandlerFound:
        err("Make sure AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are set in your env.")
        raise

    return conn


def ec2_launch(ami, instance_type, conn=None, name=None):
    """Launches an EC2 server instance"""
    require('ec2_key_pair_name')

    conn = ec2_connect()
    img = conn.get_image(ami)
    sec_grp, created = ec2_security_group(conn)

    fyi("Creating a new EC2 instance with the following parameters...")
    fyi("Image: %s (%s)" % (img.name, ami))
    fyi("Size: %s" % instance_type)
    fyi("Key Pair: %s" % env.ec2_key_pair_name)
    fyi("Security Group: %s (%s)" % (sec_grp.name, created))

    # this actually sends the request to create & start the instance. boom!
    rsrv = img.run(
        key_name=env.ec2_key_pair_name,
        instance_type=instance_type,
        security_groups=[sec_grp])
    inst = rsrv.instances[0]

    btw("Waiting for server to start booting...")

    status = 'pending'
    while status == 'pending':
        wait()
        try:
            status = inst.update()
        except boto.exception.EC2ResponseError:
            # "400 Bad Request: The instance ID '...' does not exist"
            # thrown for a short while after the img.run() call, and safely ignored
            pass

    if status == 'running':
        yay("Server is booting!")
        fyi("IP: %s" % inst.ip_address)
        fyi("DNS: %s" % inst.dns_name)

        if not name:
            name = prompt("Give this server a unique name: ", validate='[a-zA-Z0-9_-]+')
            fyi("Good choice!")

        inst.add_tag("puppet:name", name)

        return inst
    else:
        die("Couldn't launch EC2 instance. Instance status was: %s" % status)


@runs_once
def ec2_security_group(conn):
    """Fetches the right security group. If it doesn't exist, creates a security group that
    allows SSH on port 22 from any IP address.
    """
    require('ec2_security_group')

    groups = dict([(grp.name, grp) for grp in conn.get_all_security_groups()])

    try:
        return groups[env.ec2_security_group], "existing"
    except KeyError:
        # create the group
        grp = conn.create_security_group(env.ec2_security_group, "Group created for puppet.")
        grp.authorize(ip_protocol='tcp', from_port=22, to_port=22, cidr_ip='0.0.0.0/0')
        return grp, "created"


def get_puppetmaster(master=None):
    """Pass in something that probably identifies a puppet master, and get back the instance obj
        for that puppet master. Specifically, you can:
        - pass in an instance ID to get back the puppetmaster with that instance ID
        - pass in a name to get back the puppetmaster with that name (i.e. puppet:name tag)
        - pass in None and get back env.working_master if it's set (usually by an upstream task)
        - pass in None and, if env.working_master isn't set, prompt the user to pick a master
    """

    if isinstance(master, boto.ec2.instance.Instance): # no-op
        return master

    conn = ec2_connect()
    instances = itertools.chain(*[list(r.instances) for r in conn.get_all_instances()])
    masters = filter(lambda i: ('puppet:type', 'puppetmaster') in i.tags.items(), instances)

    if isinstance(master, str) or isinstance(master, unicode): # passed in an ID or name
        id_matches = filter(lambda i: i.id == master, masters) # match on id
        if len(id_matches) == 1: # match found
            return id_matches[0]
        else: # match on name
            n_matches = filter(lambda i: i.tags.get('puppet:name') == master, masters)
            if len(n_matches) == 1: # match found
                return n_matches[0]
            else: # no unique match found
                die('%s ID matches and %s name matches for instance search term "%s"' % (
                    len(id_matches), len(n_matches), master))
    elif 'working_master' in env:
        return env.working_master
    elif master == None: # prompt user to pick a master
        btw("Pick one of the following puppet masters.")
        for num, inst in enumerate(masters):
            name = inst.tags.get('puppet:name', '???')
            fyi('%s: %s "%s" [%s]' % (num, inst.id, name, inst.update()))

        key = prompt("Which master should the slave(s) be added to?: ",
            validate='[0-%s]$' % (len(masters) - 1))

        return masters[int(key)]


def wait_until_alive():
    """Stop everything until ssh is up on the server"""
    btw("Waiting for ssh to come up on the server...")

    while True:
        try:
            with settings(hide('aborts', 'running', 'stdout', 'stderr')):
                run("ls")
            yay("It's up!")
            return
        except SystemExit:
            wait()
