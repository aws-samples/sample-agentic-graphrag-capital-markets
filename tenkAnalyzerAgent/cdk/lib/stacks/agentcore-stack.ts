import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs/lib/construct';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as opensearchserverless from 'aws-cdk-lib/aws-opensearchserverless';
import * as cr from 'aws-cdk-lib/custom-resources';
import { NagSuppressions } from 'cdk-nag';
import { BaseStackProps } from '../types';

export interface AgentCoreStackProps extends BaseStackProps {
    imageUri: string
}

export class AgentCoreStack extends cdk.Stack {
    readonly agentCoreRuntime: bedrockagentcore.CfnRuntime;
    readonly agentCoreMemory: bedrockagentcore.CfnMemory;
    readonly tenkBucket: s3.Bucket;
    readonly knowledgeBase: bedrock.CfnKnowledgeBase;

    constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
        super(scope, id, props);

        const region = cdk.Stack.of(this).region;
        const accountId = cdk.Stack.of(this).account;

        /*****************************
        * VPC for Private Networking
        ******************************/

        // Create VPC with public and private subnets
        const vpc = new ec2.Vpc(this, `${props.appName}-VPC`, {
            maxAzs: 2,
            natGateways: 1,
            subnetConfiguration: [
                {
                    name: 'Public',
                    subnetType: ec2.SubnetType.PUBLIC,
                    cidrMask: 24,
                },
                {
                    name: 'Private',
                    subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidrMask: 24,
                },
            ],
        });

        // S3 Gateway Endpoint (free, for S3 access without NAT)
        vpc.addGatewayEndpoint(`${props.appName}-S3Endpoint`, {
            service: ec2.GatewayVpcEndpointAwsService.S3,
        });

        // VPC Flow Logs for network monitoring
        const flowLogGroup = new logs.LogGroup(this, `${props.appName}-VPCFlowLogGroup`, {
            retention: logs.RetentionDays.ONE_WEEK,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        const flowLogRole = new iam.Role(this, `${props.appName}-VPCFlowLogRole`, {
            assumedBy: new iam.ServicePrincipal('vpc-flow-logs.amazonaws.com'),
        });

        vpc.addFlowLog(`${props.appName}-FlowLog`, {
            destination: ec2.FlowLogDestination.toCloudWatchLogs(flowLogGroup, flowLogRole),
            trafficType: ec2.FlowLogTrafficType.REJECT, // Log only rejected traffic to reduce cost
        });

        /*****************************
        * S3 Bucket for TigerGraph Staging
        ******************************/

        const stagingBucket = new s3.Bucket(this, `${props.appName}-StagingBucket`, {
            encryption: s3.BucketEncryption.S3_MANAGED,
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            autoDeleteObjects: true,
            enforceSSL: true, // Require SSL for all requests
        });

        /*****************************
        * S3 Bucket for 10-K Documents
        ******************************/

        this.tenkBucket = new s3.Bucket(this, `${props.appName}-TenkBucket`, {
            encryption: s3.BucketEncryption.S3_MANAGED,
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            removalPolicy: cdk.RemovalPolicy.RETAIN, // Preserve data on stack deletion
            enforceSSL: true, // Require SSL for all requests
        });

        /*****************************
        * OpenSearch Serverless for Knowledge Base
        ******************************/

        // AOSS names must be 3-32 chars, lowercase, start with letter
        const collectionName = 'tenk-kb-col';

        // 1. Encryption Policy (required before collection)
        const encryptionPolicy = new cdk.CfnResource(this, `${props.appName}-AOSSEncryptionPolicy`, {
            type: 'AWS::OpenSearchServerless::SecurityPolicy',
            properties: {
                Name: 'tenk-kb-encrypt',
                Type: 'encryption',
                Policy: JSON.stringify({
                    Rules: [
                        {
                            ResourceType: 'collection',
                            Resource: [`collection/${collectionName}`],
                        },
                    ],
                    AWSOwnedKey: true,
                }),
            },
        });

        // 2. Network Policy (required before collection)
        // NOTE: AllowFromPublic enables public network access, but data access is strictly
        // controlled by IAM policies. The Data Access Policy (below) restricts access to:
        // - Knowledge Base service role (for Bedrock integration)
        // - AWS account root (for CloudFormation operations)
        // Random internet users CANNOT access the collection without valid AWS credentials
        // and explicit permissions in the data access policy. This is standard for
        // Bedrock Knowledge Base integrations. For enhanced security, use VPC endpoints.
        const networkPolicy = new cdk.CfnResource(this, `${props.appName}-AOSSNetworkPolicy`, {
            type: 'AWS::OpenSearchServerless::SecurityPolicy',
            properties: {
                Name: 'tenk-kb-network',
                Type: 'network',
                Policy: JSON.stringify([
                    {
                        Rules: [
                            {
                                ResourceType: 'collection',
                                Resource: [`collection/${collectionName}`],
                            },
                            {
                                ResourceType: 'dashboard',
                                Resource: [`collection/${collectionName}`],
                            },
                        ],
                        AllowFromPublic: true,
                    },
                ]),
            },
        });

        // 3. OpenSearch Serverless Collection
        const aossCollection = new cdk.CfnResource(this, `${props.appName}-AOSSCollection`, {
            type: 'AWS::OpenSearchServerless::Collection',
            properties: {
                Name: collectionName,
                Type: 'VECTORSEARCH',
                Description: 'Vector store for Bedrock Knowledge Base',
            },
        });

        // Collection depends on policies
        aossCollection.addDependency(encryptionPolicy);
        aossCollection.addDependency(networkPolicy);

        const collectionArn = aossCollection.getAtt('Arn').toString();

        /*****************************
        * Bedrock Knowledge Base
        ******************************/

        // IAM role for Knowledge Base to access S3 and AOSS
        const kbRole = new iam.Role(this, `${props.appName}-KnowledgeBaseRole`, {
            assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com'),
            description: 'IAM role for Bedrock Knowledge Base to access S3 and OpenSearch Serverless',
        });

        this.tenkBucket.grantRead(kbRole);

        // Grant AOSS permissions to KB role
        kbRole.addToPolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ['aoss:APIAccessAll'],
                resources: [collectionArn],
            })
        );

        // Grant KB role permission to invoke embedding model
        kbRole.addToPolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ['bedrock:InvokeModel'],
                resources: [`arn:aws:bedrock:${region}::foundation-model/amazon.titan-embed-text-v2:0`],
            })
        );

        // 4. Data Access Policy (grant KB role + CloudFormation access to AOSS collection)
        const dataAccessPolicy = new cdk.CfnResource(this, `${props.appName}-AOSSDataAccessPolicy`, {
            type: 'AWS::OpenSearchServerless::AccessPolicy',
            properties: {
                Name: `${collectionName}-data-access`,
                Type: 'data',
                Policy: cdk.Fn.sub(
                    JSON.stringify([
                        {
                            Rules: [
                                {
                                    ResourceType: 'collection',
                                    Resource: [`collection/${collectionName}`],
                                    Permission: ['aoss:*'],
                                },
                                {
                                    ResourceType: 'index',
                                    Resource: [`index/${collectionName}/*`],
                                    Permission: ['aoss:*'],
                                },
                            ],
                            Principal: [
                                '${KBRoleArn}',
                                'arn:aws:iam::${AWS::AccountId}:root', // Allow CloudFormation to create index
                            ],
                        },
                    ]),
                    { KBRoleArn: kbRole.roleArn }
                ),
            },
        });

        // Data access policy depends on collection
        dataAccessPolicy.addDependency(aossCollection);

        // Wait for collection to be ACTIVE before creating index
        const waitForCollection = new cr.AwsCustomResource(this, `${props.appName}-WaitForCollection`, {
            onCreate: {
                service: 'OpenSearchServerless',
                action: 'batchGetCollection',
                parameters: {
                    names: [collectionName],
                },
                physicalResourceId: cr.PhysicalResourceId.of('WaitForCollection'),
            },
            policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
                resources: cr.AwsCustomResourcePolicy.ANY_RESOURCE,
            }),
        });
        waitForCollection.node.addDependency(aossCollection);
        waitForCollection.node.addDependency(dataAccessPolicy);

        // Create vector index for Knowledge Base
        const vectorIndexName = 'bedrock-knowledge-base-default-index';
        const vectorIndex = new opensearchserverless.CfnIndex(this, `${props.appName}-VectorIndex`, {
            collectionEndpoint: aossCollection.getAtt('CollectionEndpoint').toString(),
            indexName: vectorIndexName,
            settings: {
                index: {
                    knn: true, // Required for vector search
                },
            },
            mappings: {
                properties: {
                    'bedrock-knowledge-base-default-vector': {
                        type: 'knn_vector',
                        dimension: 1024, // Amazon Titan Embed Text v2
                        method: {
                            name: 'hnsw',
                            engine: 'faiss',
                            spaceType: 'l2',
                            parameters: {
                                efConstruction: 128, // AWS best practice
                                m: 24, // AWS best practice
                            },
                        },
                    },
                    'AMAZON_BEDROCK_METADATA': {
                        type: 'text',
                        index: false,
                    },
                    'AMAZON_BEDROCK_TEXT_CHUNK': {
                        type: 'text',
                    },
                },
            },
        });
        vectorIndex.node.addDependency(waitForCollection);

        // Knowledge Base
        this.knowledgeBase = new bedrock.CfnKnowledgeBase(this, `${props.appName}-KnowledgeBase`, {
            name: `${props.appName}-KB`,
            roleArn: kbRole.roleArn,
            knowledgeBaseConfiguration: {
                type: 'VECTOR',
                vectorKnowledgeBaseConfiguration: {
                    embeddingModelArn: `arn:aws:bedrock:${region}::foundation-model/amazon.titan-embed-text-v2:0`,
                },
            },
            storageConfiguration: {
                type: 'OPENSEARCH_SERVERLESS',
                opensearchServerlessConfiguration: {
                    collectionArn: collectionArn,
                    vectorIndexName: vectorIndexName,
                    fieldMapping: {
                        vectorField: 'bedrock-knowledge-base-default-vector',
                        textField: 'AMAZON_BEDROCK_TEXT_CHUNK',
                        metadataField: 'AMAZON_BEDROCK_METADATA',
                    },
                },
            },
        });

        // KB depends on vector index
        this.knowledgeBase.node.addDependency(vectorIndex);

        // S3 Data Source for Knowledge Base
        const dataSource = new bedrock.CfnDataSource(this, `${props.appName}-DataSource`, {
            knowledgeBaseId: this.knowledgeBase.attrKnowledgeBaseId,
            name: `${props.appName}-S3DataSource`,
            dataSourceConfiguration: {
                type: 'S3',
                s3Configuration: {
                    bucketArn: this.tenkBucket.bucketArn,
                    inclusionPrefixes: ['tenks/'],
                },
            },
        });

        /*****************************
        * Security Groups
        ******************************/

        // Security Group for TigerGraph - allows inbound only from AgentCore
        const tigergraphSG = new ec2.SecurityGroup(this, `${props.appName}-TigerGraphSG`, {
            vpc,
            description: 'Security group for TigerGraph EC2 instance',
            allowAllOutbound: true,
        });

        // Security Group for AgentCore - allows outbound to TigerGraph
        const agentCoreSG = new ec2.SecurityGroup(this, `${props.appName}-AgentCoreSG`, {
            vpc,
            description: 'Security group for AgentCore runtime',
            allowAllOutbound: true,
        });

        // Allow AgentCore to connect to TigerGraph on port 14240
        tigergraphSG.addIngressRule(
            agentCoreSG,
            ec2.Port.tcp(14240),
            'Allow AgentCore to connect to TigerGraph'
        );

        /*****************************
        * TigerGraph EC2 Instance
        ******************************/

        // IAM Role for EC2 instance
        const ec2Role = new iam.Role(this, `${props.appName}-TigerGraphEC2Role`, {
            assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
            description: 'IAM role for TigerGraph EC2 instance',
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
            ],
        });

        // Grant S3 access to staging bucket
        stagingBucket.grantRead(ec2Role);

        // Create TigerGraph EC2 instance
        const tigergraphInstance = new ec2.Instance(this, `${props.appName}-TigerGraphInstance`, {
            vpc,
            vpcSubnets: {
                subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
            },
            instanceType: ec2.InstanceType.of(ec2.InstanceClass.T2, ec2.InstanceSize.XLARGE),
            machineImage: ec2.MachineImage.latestAmazonLinux2023(),
            securityGroup: tigergraphSG,
            role: ec2Role,
            blockDevices: [
                {
                    deviceName: '/dev/xvda',
                    volume: ec2.BlockDeviceVolume.ebs(30, {
                        encrypted: true,
                        volumeType: ec2.EbsDeviceVolumeType.GP3,
                    }),
                },
            ],
        });

        /*****************************
        * Bedrock Guardrails
        ******************************/

        // Create guardrail for AI safety controls
        const guardrail = new bedrock.CfnGuardrail(this, `${props.appName}-Guardrail`, {
            name: `${props.appName}-Guardrail`,
            description: 'Guardrails for financial analysis agent - content filtering and PII protection',
            blockedInputMessaging: 'Input blocked by guardrails for safety reasons.',
            blockedOutputsMessaging: 'Response blocked by guardrails for safety reasons.',
            
            // Content filtering - block harmful content
            contentPolicyConfig: {
                filtersConfig: [
                    { type: 'HATE', inputStrength: 'HIGH', outputStrength: 'HIGH' },
                    { type: 'VIOLENCE', inputStrength: 'HIGH', outputStrength: 'HIGH' },
                    { type: 'SEXUAL', inputStrength: 'HIGH', outputStrength: 'HIGH' },
                    { type: 'MISCONDUCT', inputStrength: 'HIGH', outputStrength: 'HIGH' },
                    { type: 'PROMPT_ATTACK', inputStrength: 'HIGH', outputStrength: 'NONE' },
                ],
            },

            // PII protection - anonymize financial and personal information
            sensitiveInformationPolicyConfig: {
                piiEntitiesConfig: [
                    { type: 'US_SOCIAL_SECURITY_NUMBER', action: 'ANONYMIZE' },
                    { type: 'US_BANK_ACCOUNT_NUMBER', action: 'ANONYMIZE' },
                    { type: 'CREDIT_DEBIT_CARD_NUMBER', action: 'ANONYMIZE' },
                    { type: 'EMAIL', action: 'ANONYMIZE' },
                    { type: 'PHONE', action: 'ANONYMIZE' },
                    { type: 'AWS_ACCESS_KEY', action: 'BLOCK' },
                    { type: 'AWS_SECRET_KEY', action: 'BLOCK' },
                ],
            },
        });

        /*****************************
        * AgentCore Memory
        ******************************/

        this.agentCoreMemory = new bedrockagentcore.CfnMemory(this, `${props.appName}-AgentCoreMemory`, {
            name: "tenkAnalyzerAgent_Memory",
            eventExpiryDuration: 30,
            description: "Memory resource with 30 days event expiry",
            memoryStrategies: [
                // can take a built-in strategy from https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/built-in-strategies.html or define a custom one
            ],
        });
        
        /*****************************
        * AgentCore Runtime
        ******************************/

        // taken from https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html#runtime-permissions-execution
        const runtimePolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    sid: 'ECRImageAccess',
                    effect: iam.Effect.ALLOW,
                    actions: ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer'],
                    resources: [
                        `arn:aws:ecr:${region}:${accountId}:repository/*`,
                    ],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ['logs:DescribeLogStreams', 'logs:CreateLogGroup'],
                    resources: [
                        `arn:aws:logs:${region}:${accountId}:log-group:/aws/bedrock-agentcore/runtimes/*`,
                    ],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ['logs:DescribeLogGroups'],
                    resources: [
                        `arn:aws:logs:${region}:${accountId}:log-group:*`,
                    ],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
                    resources: [
                        `arn:aws:logs:${region}:${accountId}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`,
                    ],
                }),
                new iam.PolicyStatement({
                    sid: 'ECRTokenAccess',
                    effect: iam.Effect.ALLOW,
                    actions: ['ecr:GetAuthorizationToken'],
                    resources: ['*'],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: [
                        'xray:PutTraceSegments',
                        'xray:PutTelemetryRecords',
                        'xray:GetSamplingRules',
                        'xray:GetSamplingTargets',
                    ],
                resources: ['*'],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ['cloudwatch:PutMetricData'],
                    resources: ['*'],
                    conditions: {
                        StringEquals: { 'cloudwatch:namespace': 'bedrock-agentcore' },
                    },
                }),
                new iam.PolicyStatement({
                    sid: 'GetAgentAccessToken',
                    effect: iam.Effect.ALLOW,
                    actions: [
                        'bedrock-agentcore:GetWorkloadAccessToken',
                        'bedrock-agentcore:GetWorkloadAccessTokenForJWT',
                        'bedrock-agentcore:GetWorkloadAccessTokenForUserId',
                    ],
                    resources: [
                        `arn:aws:bedrock-agentcore:${region}:${accountId}:workload-identity-directory/default`,
                        `arn:aws:bedrock-agentcore:${region}:${accountId}:workload-identity-directory/default/workload-identity/agentName-*`,
                    ],
                }),
                // NOTE: Demo-grade permissions - Broad access to Bedrock resources
                // For production, restrict to specific models and resources needed
                new iam.PolicyStatement({
                    sid: 'BedrockModelInvocation',
                    effect: iam.Effect.ALLOW,
                    actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
                    resources: [
                        `arn:aws:bedrock:*::foundation-model/*`, // All foundation models
                        `arn:aws:bedrock:${region}:${accountId}:*`, // All Bedrock resources in account
                        // Production recommendation: Restrict to specific models, e.g.:
                        // `arn:aws:bedrock:${region}::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0`
                    ],
                }),
                // Guardrails - Allow agent to apply guardrails
                new iam.PolicyStatement({
                    sid: 'BedrockGuardrailAccess',
                    effect: iam.Effect.ALLOW,
                    actions: ['bedrock:ApplyGuardrail'],
                    resources: [guardrail.attrGuardrailArn],
                }),
                // NOTE: Demo-grade permissions - Access to all Knowledge Bases in account
                // For production, scope to specific Knowledge Base ARN
                new iam.PolicyStatement({
                    sid: 'BedrockKnowledgeBaseAccess',
                    effect: iam.Effect.ALLOW,
                    actions: ['bedrock:Retrieve', 'bedrock:RetrieveAndGenerate'],
                    resources: [
                        `arn:aws:bedrock:${region}:${accountId}:knowledge-base/*`,
                        // Production recommendation: Use specific KB ARN:
                        // this.knowledgeBase.attrKnowledgeBaseArn
                    ],
                }),
                // NOTE: Demo-grade permissions - All memory operations
                // For production, enumerate specific actions: CreateEvent, ListEvents, 
                // RetrieveEvents, StoreEvents, CreateSession, GetSession, etc.
                new iam.PolicyStatement({
                    sid: 'AgentCoreMemoryAccess',
                    effect: iam.Effect.ALLOW,
                    actions: ['bedrock-agentcore:*'],
                    resources: [
                        `arn:aws:bedrock-agentcore:${region}:${accountId}:memory/*`,
                    ],
                }),
            ],
        });

        const runtimeRole = new iam.Role(this, `${props.appName}-AgentCoreRuntimeRole`, {
            assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
            description: 'IAM role for Bedrock AgentCore Runtime',
            inlinePolicies: {
                RuntimeAccessPolicy: runtimePolicy
            }
        });

        this.agentCoreRuntime = new bedrockagentcore.CfnRuntime(this, `${props.appName}-AgentCoreRuntime`, {
            agentRuntimeArtifact: {
                containerConfiguration: {
                    containerUri: props.imageUri
                }
            },
            agentRuntimeName: "tenkAnalyzerAgent_Agent",
            protocolConfiguration: "HTTP",
            networkConfiguration: {
                networkMode: "VPC",
                networkModeConfig: {
                    subnets: vpc.selectSubnets({ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }).subnetIds,
                    securityGroups: [agentCoreSG.securityGroupId],
                }
            },
            roleArn: runtimeRole.roleArn,
            environmentVariables: {
                // AWS Configuration
                "AWS_REGION": region,
                
                // Memory Configuration
                "BEDROCK_AGENTCORE_MEMORY_ID":  this.agentCoreMemory.attrMemoryId,
                
                // Knowledge Base Configuration
                "TENK_KB_ID": this.knowledgeBase.attrKnowledgeBaseId,
                
                // Guardrail Configuration
                "GUARDRAIL_ID": guardrail.attrGuardrailId,
                "GUARDRAIL_VERSION": "DRAFT",
                
                // TigerGraph Configuration
                // NOTE: Demo-grade setup - TG runs on EC2, credentials hardcoded
                // For production: Use AWS Secrets Manager and private TG endpoint
                "TG_HOST": `http://${tigergraphInstance.instancePrivateIp}:14240`,
                "TG_USERNAME": "tigergraph",
                "TG_PASSWORD": "tigergraph",
                "TG_GRAPHNAME": "CapMarkets",
            }
        });

        // DEFAULT endpoint is automatically created and always points to the newest published version
        // Custom endpoints (PROD/DEV) can be created manually after runtime is in READY state if needed
        // https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agent-runtime-versioning.html

        /*****************************
        * Outputs
        ******************************/

        new cdk.CfnOutput(this, 'StagingBucketName', {
            value: stagingBucket.bucketName,
            description: 'S3 bucket for TigerGraph docker image and data files',
            exportName: `${props.appName}-StagingBucketName`,
        });

        new cdk.CfnOutput(this, 'TigerGraphInstanceId', {
            value: tigergraphInstance.instanceId,
            description: 'EC2 instance ID for TigerGraph (use with SSM)',
            exportName: `${props.appName}-TigerGraphInstanceId`,
        });

        new cdk.CfnOutput(this, 'TigerGraphPrivateIp', {
            value: tigergraphInstance.instancePrivateIp,
            description: 'Private IP of TigerGraph EC2 instance',
            exportName: `${props.appName}-TigerGraphPrivateIp`,
        });

        new cdk.CfnOutput(this, 'TenkBucketName', {
            value: this.tenkBucket.bucketName,
            description: 'S3 bucket name for 10-K documents',
            exportName: `${props.appName}-TenkBucketName`,
        });

        new cdk.CfnOutput(this, 'KnowledgeBaseId', {
            value: this.knowledgeBase.attrKnowledgeBaseId,
            description: 'Bedrock Knowledge Base ID',
            exportName: `${props.appName}-KnowledgeBaseId`,
        });

        new cdk.CfnOutput(this, 'DataSourceId', {
            value: dataSource.attrDataSourceId,
            description: 'Knowledge Base Data Source ID (for syncing)',
            exportName: `${props.appName}-DataSourceId`,
        });

        new cdk.CfnOutput(this, 'AgentRuntimeArn', {
            value: this.agentCoreRuntime.attrAgentRuntimeArn,
            description: 'Bedrock AgentCore Runtime ARN (for notebook)',
            exportName: `${props.appName}-AgentRuntimeArn`,
        });

        /*****************************
        * CDK-Nag Suppressions
        ******************************/

        // Suppress demo-grade patterns with documentation for AWS sample repo
        NagSuppressions.addStackSuppressions(this, [
            {
                id: 'AwsSolutions-OS08',
                reason: 'OpenSearch Serverless network policy allows public access (AllowFromPublic: true), but data access is strictly controlled by IAM-based data access policies. The collection is only accessible to the Knowledge Base service role and AWS account root (for CloudFormation operations). Anonymous internet access is not possible - valid AWS credentials and explicit IAM permissions are required. This configuration is standard for Bedrock Knowledge Base integrations. For enhanced security in production, VPC endpoints can be used instead of public access.',
            },
            {
                id: 'AwsSolutions-S1',
                reason: 'Demo: S3 access logging not required for sample application. Production deployments should enable server access logging per README security guidance.',
            },
            {
                id: 'AwsSolutions-EC28',
                reason: 'Demo: EC2 detailed monitoring adds cost without benefit for sample. Production should enable detailed monitoring for TigerGraph instance.',
            },
            {
                id: 'AwsSolutions-EC29',
                reason: 'Demo: TigerGraph EC2 instance is ephemeral for sample. Production should enable termination protection.',
            },
            {
                id: 'AwsSolutions-IAM4',
                reason: 'CDK-generated Lambda custom resource uses AWS managed policy (AWSLambdaBasicExecutionRole). EC2 instance uses AmazonSSMManagedInstanceCore for Systems Manager access, which is AWS best practice.',
                appliesTo: [
                    'Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
                    'Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonSSMManagedInstanceCore',
                ],
            },
            {
                id: 'AwsSolutions-L1',
                reason: 'CDK-generated Lambda custom resource runtime is managed by CDK framework.',
            },
            {
                id: 'AwsSolutions-IAM5',
                reason: 'Demo: Wildcard permissions used for flexibility and rapid prototyping. Production deployments should scope IAM permissions to specific resources per README security guidance.',
                appliesTo: [
                    'Action::s3:GetBucket*',
                    'Action::s3:GetObject*',
                    'Action::s3:List*',
                    'Resource::*',
                    'Resource::<tenkAnalyzerAgentStagingBucketF3D90582.Arn>/*',
                    'Resource::<tenkAnalyzerAgentTenkBucket4C8D6AD6.Arn>/*',
                    'Resource::arn:aws:ecr:<AWS::Region>:<AWS::AccountId>:repository/*',
                    'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*',
                    'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:*',
                    'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*',
                    'Resource::arn:aws:bedrock-agentcore:<AWS::Region>:<AWS::AccountId>:workload-identity-directory/default/workload-identity/agentName-*',
                    'Resource::arn:aws:bedrock:*::foundation-model/*',
                    'Resource::arn:aws:bedrock:<AWS::Region>:<AWS::AccountId>:*',
                    'Resource::arn:aws:bedrock:<AWS::Region>:<AWS::AccountId>:knowledge-base/*',
                    'Action::bedrock-agentcore:*',
                    'Resource::arn:aws:bedrock-agentcore:<AWS::Region>:<AWS::AccountId>:memory/*',
                ],
            },
        ]);
    }
}
