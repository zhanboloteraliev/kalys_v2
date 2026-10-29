# Deploying Kalys to AWS

One `t3.medium` EC2 instance running everything: PostgreSQL in Docker, the
Chainlit app as a systemd service, and Bedrock reached through an IAM role
attached to the instance.

The app is **not** exposed to the internet. You reach it through an SSH
tunnel. That keeps port 8000 closed, which matters because every question
costs Bedrock money - an open endpoint is a way for strangers and bots to
spend it.

## Why you run the console steps, not the AI assistant

The `kalysdev` IAM user is deliberately limited to Bedrock (see CLAUDE.md).
It cannot create instances, roles, or security groups, and we are keeping it
that way: a key that can only call one API is a key that can only ever cost
one thing. So steps 1-3 below are yours, in the AWS Console.

## 1. Create the IAM role for the instance

The server must never hold a copy of your access keys. An instance role gives
it short-lived credentials automatically, and boto3 finds them with no code
change.

1. IAM -> Policies -> Create policy -> JSON, paste this, name it
   `KalysBedrockInvoke`:

   ```json
   {
       "Version": "2012-10-17",
       "Statement": [
           {
               "Effect": "Allow",
               "Action": [
                   "bedrock:InvokeModel",
                   "bedrock:InvokeModelWithResponseStream"
               ],
               "Resource": [
                   "arn:aws:bedrock:*::foundation-model/anthropic.*",
                   "arn:aws:bedrock:*:*:inference-profile/us.anthropic.*"
               ]
           }
       ]
   }
   ```

   Narrower than the laptop policy on purpose: the server only answers
   questions, so it does not need `ListFoundationModels` or
   `GetFoundationModel`. Both resource lines are needed - the model call goes
   through the `us.` cross-region inference profile, which is a separate ARN
   from the model itself (see LEARNING.md, Step 7).

2. IAM -> Roles -> Create role -> AWS service -> EC2, attach
   `KalysBedrockInvoke`, name it `KalysServerRole`.

## 1b. Temporary EC2 policy on `kalysdev` (optional)

Only needed if you want the assistant to create the key pair, security group,
and instance for you instead of doing steps 2-3 in the console. Attach this to
`kalysdev` as `KalysDeployTemporary`, and **detach it once the instance is
running** - `kalysdev` should go back to Bedrock-only.

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "Lookups",
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeImages",
                "ec2:DescribeInstances",
                "ec2:DescribeInstanceStatus",
                "ec2:DescribeInstanceTypes",
                "ec2:DescribeVpcs",
                "ec2:DescribeSubnets",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeKeyPairs"
            ],
            "Resource": "*"
        },
        {
            "Sid": "CreateAndRunInUsEast1Only",
            "Effect": "Allow",
            "Action": [
                "ec2:CreateKeyPair",
                "ec2:CreateSecurityGroup",
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:CreateTags",
                "ec2:RunInstances",
                "ec2:StartInstances",
                "ec2:StopInstances",
                "ec2:AssociateIamInstanceProfile"
            ],
            "Resource": "*",
            "Condition": {
                "StringEquals": { "aws:RequestedRegion": "us-east-1" }
            }
        },
        {
            "Sid": "PassOnlyTheServerRole",
            "Effect": "Allow",
            "Action": "iam:PassRole",
            "Resource": "arn:aws:iam::787013172660:role/KalysServerRole"
        }
    ]
}
```

What it deliberately leaves out, and why:

- **No `iam:CreateRole` or `iam:AttachRolePolicy`.** With those, this key could
  build a role holding `AdministratorAccess` and attach it to an instance -
  which is account admin by another name. That is why step 1 stays a console
  step. `iam:PassRole` is limited to the one role ARN, which is safe: it can
  hand out that role and nothing else.
- **No `ec2:TerminateInstances` or `ec2:DeleteSecurityGroup`.** The key can
  create and start/stop, but cannot destroy the instance or the database on it.
- **Locked to `us-east-1`**, so nothing can be launched in another region by
  mistake and quietly bill you.

## 2. Create the security group

EC2 -> Security Groups -> Create, name `kalys-sg`:

- Inbound: **SSH (22) from My IP only**. Nothing else. No port 8000.
- Outbound: leave the default (all traffic) - the instance needs to reach
  GitHub, PyPI, Hugging Face, and Bedrock.

## 3. Launch the instance

EC2 -> Launch instance:

| Setting | Value |
| --- | --- |
| Name | `kalys` |
| AMI | Ubuntu Server 24.04 LTS or newer (26.04 works) | 
| Type | `t3.medium` (4 GB RAM) |
| Key pair | create one, download the `.pem`, `chmod 400` it |
| Security group | `kalys-sg` from step 2 |
| Storage | 30 GB gp3 |
| IAM instance profile | `KalysServerRole` from step 1 (under Advanced details) |
| Region | `us-east-1` |

Why these: the setup script has uv install its own Python 3.12, so the
Ubuntu version does not matter (26.04 ships Python 3.14, which PyTorch has no
stable wheels for yet).
`t3.medium` is the smallest type that fits the local embedding model next to
PostgreSQL - `t3.micro` (1 GB, the free-tier type) cannot. 30 GB holds the
OS, the 1 GB model, PyTorch, and the database with room to spare.

**It only costs money while it is running.** Stop it when you are not
testing; stopping keeps the disk and costs a few dollars a month, while
running it all the time is about $30.

## 4. Copy the database up

The site blocks cloud servers (403, see `docs/cbd-api.md`), so the server
cannot download the law itself. Move the database instead.

On your laptop, regenerate the dump if the data has changed:

```bash
docker compose up -d db
docker compose exec -T db pg_dump -U kalys -d kalys -Fc > data/deploy/kalys.dump
```

Then copy it up (about 40 MB):

```bash
scp -i <your-key.pem> data/deploy/kalys.dump ubuntu@<instance-ip>:~/kalys.dump
```

This is the first-time path only. Port 22 is closed now (step 7e), so for
later updates use section 4b instead.

## 4b. Updating the database later, through SSM

With no SSH there is no way to copy a file up - but there is no need to. SSM
forwards a local port straight to PostgreSQL on the instance, and `pg_restore`
runs on the laptop, writing over the network:

```bash
# terminal 1: local port 5433 -> the instance's 127.0.0.1:5432
aws ssm start-session --target i-0aa39205f5024aa85 \
    --document-name AWS-StartPortForwardingSession \
    --parameters '{"portNumber":["5432"],"localPortNumber":["5433"]}'

# terminal 2
docker compose exec -T db pg_dump -U kalys -d kalys -Fc > data/deploy/kalys.dump
docker run --rm -i --network host -e PGPASSWORD=kalys pgvector/pgvector:pg16 \
    pg_restore -h 127.0.0.1 -p 5433 -U kalys -d kalys \
    --clean --if-exists --no-owner --no-privileges < data/deploy/kalys.dump
```

Four things worth knowing:

- **The dump is the whole database, including `embeddings`.** Both sides run
  the same Titan model, so the vectors are portable and re-running
  `python -m kalys.embed` on the box is unnecessary - it saves 20 minutes and
  a Bedrock run. (It is still the right thing to run when the *model* changes,
  as in Step 16.)
- **`usage` is replaced too**, and that table is the daily spending cap's
  memory. Save it first and put it back, or a restore silently hands out a
  fresh day's budget:

  ```bash
  psql ... -At -F, -c "select day, questions from usage order by day" > usage.csv
  # after the restore, re-insert with
  # ON CONFLICT (day) DO UPDATE SET questions = GREATEST(usage.questions, EXCLUDED.questions)
  ```
- **No restart is needed for a data-only change.** `web.py` and `answer.py`
  open a fresh connection per question, so there are no cached query plans
  pointing at the tables the restore dropped, and the next question sees the
  new data. Restart only when the *code* changed.
- **Mount the dump with `-i` and a redirect, not `-v`.** On Fedora, SELinux
  labels a bind-mounted file so the container cannot read it
  (`could not open input file: Permission denied`); piping it in on stdin
  avoids the whole question.

## 5. Run the setup script

```bash
ssh -i <your-key.pem> ubuntu@<instance-ip>
git clone --branch claude/kalys-v2-rebuild-d75xqd \
    https://github.com/zhanboloteraliev/kalys_v2.git
./kalys_v2/deploy/setup.sh ~/kalys.dump
```

It installs Docker, uv, and Python, starts PostgreSQL, restores the dump,
downloads the embedding model, and installs the `kalys` systemd service. It
takes several minutes, mostly the model download, and is safe to re-run.

## 6. Open the app

From your laptop:

```bash
ssh -i <your-key.pem> -L 8000:localhost:8000 ubuntu@<instance-ip>
```

Leave that running and open <http://localhost:8000>.

## 7. Optional: reach the instance through SSM instead of SSH

Session Manager connects through the AWS API rather than over the network.
The agent on the instance opens an outbound connection to AWS, and you are
relayed through it, so **the security group needs no inbound rules at all** -
not even port 22. Your own IP address stops mattering, and there is no key
file to protect.

Ubuntu's AWS images already run the SSM agent, so only permissions are
missing.

### 7a. Let the agent register (console)

IAM -> Roles -> `KalysServerRole` -> Add permissions -> Attach policies ->
attach the AWS-managed **`AmazonSSMManagedInstanceCore`**.

Until this is attached the agent is running but cannot talk to AWS, and the
instance will not appear in Systems Manager -> Fleet Manager.

### 7b. Let yourself start sessions (console)

IAM -> Users -> `kalysdev` -> Add permissions -> Create inline policy -> JSON,
named `KalysSsmSession`. Scoped to this one instance:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "StartSessionOnKalysOnly",
            "Effect": "Allow",
            "Action": "ssm:StartSession",
            "Resource": [
                "arn:aws:ec2:us-east-1:787013172660:instance/i-0aa39205f5024aa85",
                "arn:aws:ssm:*:*:document/AWS-StartPortForwardingSession",
                "arn:aws:ssm:*:*:document/SSM-SessionManagerRunShell"
            ]
        },
        {
            "Sid": "ManageOwnSessions",
            "Effect": "Allow",
            "Action": [
                "ssm:TerminateSession",
                "ssm:ResumeSession"
            ],
            "Resource": "arn:aws:ssm:*:*:session/${aws:username}-*"
        },
        {
            "Sid": "Lookups",
            "Effect": "Allow",
            "Action": [
                "ssm:DescribeSessions",
                "ssm:DescribeInstanceInformation",
                "ssm:GetConnectionStatus"
            ],
            "Resource": "*"
        }
    ]
}
```

### 7c. Install the plugin (laptop)

The AWS CLI needs a helper binary for sessions. Without root:

```bash
curl -sS "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/linux_64bit/session-manager-plugin.rpm" -o smp.rpm
rpm2cpio smp.rpm | cpio -idm
cp usr/local/sessionmanagerplugin/bin/session-manager-plugin ~/.local/bin/
chmod +x ~/.local/bin/session-manager-plugin
```

### 7d. Use it

```bash
# a shell on the instance, no SSH
aws ssm start-session --target i-0aa39205f5024aa85

# the chat on http://localhost:8000, no SSH
aws ssm start-session --target i-0aa39205f5024aa85 \
    --document-name AWS-StartPortForwardingSession \
    --parameters '{"portNumber":["8000"],"localPortNumber":["8000"]}'
```

### 7e. Close port 22

Only once the two commands above work. EC2 -> Security Groups -> `kalys-sg`
-> Inbound rules -> Edit -> delete the SSH rule -> Save. Keep the `.pem` file
as a way back in if SSM ever breaks: you can re-add the rule from the console.

## 8. Read-only access for troubleshooting

Without this, diagnosing anything in AWS turns into a slow loop: the
assistant hits an error, cannot see why, and has to ask the owner to go and
read the console. Attaching a read-only policy to `kalysdev` removes that
loop entirely.

This is safe in a way that write access is not. Nothing here can create,
change, or delete anything. IAM read in particular never returns secret
access keys - AWS does not expose them through any API, only at creation
time.

IAM -> Users -> `kalysdev` -> Add permissions -> Create inline policy ->
JSON, named `KalysReadOnlyTroubleshoot`:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "SeeWhoCanDoWhat",
            "Effect": "Allow",
            "Action": [
                "iam:Get*",
                "iam:List*",
                "iam:SimulatePrincipalPolicy"
            ],
            "Resource": "*"
        },
        {
            "Sid": "SeeComputeAndSsmState",
            "Effect": "Allow",
            "Action": [
                "ec2:Describe*",
                "ssm:Describe*",
                "ssm:GetConnectionStatus",
                "ssm:ListTagsForResource",
                "cloudtrail:LookupEvents"
            ],
            "Resource": "*"
        }
    ]
}
```

`iam:SimulatePrincipalPolicy` is the useful one: it answers "is this role
allowed to perform this action?" directly, instead of guessing from an error
message.

Deliberately left out: `secretsmanager:*` and `ssm:GetParameter*`, because
read access to those returns the secret values themselves rather than just
configuration. Everything above returns settings, never credentials.

## Running it day to day

On the instance:

```bash
sudo journalctl -u kalys -f      # live logs
sudo systemctl restart kalys     # after a git pull
cd kalys_v2 && git pull && sudo systemctl restart kalys
```

To stop paying for compute, stop the instance in the console (**Stop**, not
Terminate - Terminate deletes the disk and the database with it). After a
start, the public IP changes unless you attach an Elastic IP, so check it
before you SSH.

## If something is wrong

| Symptom | Likely cause |
| --- | --- |
| `NoCredentialsError`, or Bedrock access denied | The instance profile is missing, or `.env` wrongly sets `AWS_PROFILE`. The server must not set it. |
| `ValidationException` about on-demand throughput | The model id lost its `us.` inference-profile prefix. |
| Service restarts in a loop | `sudo journalctl -u kalys -n 50`. Usually PostgreSQL is not up yet, or out of memory. |
| "No matching articles" for every question | The dump did not restore. Check `SELECT count(*) FROM embeddings` is 3295. |
| Tunnel connects but the page never loads | The service is not running: `sudo systemctl status kalys`. |
