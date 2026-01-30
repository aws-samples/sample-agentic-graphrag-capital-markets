#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { Aspects } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { BaseStackProps } from '../lib/types';
import {
  DockerImageStack,
  AgentCoreStack
} from '../lib/stacks';

const app = new cdk.App();
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
const deploymentProps: BaseStackProps = {
  appName: "tenkAnalyzerAgent",
  // env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION },

  /* For more information, see https://docs.aws.amazon.com/cdk/latest/guide/environments.html */
}
const dockerImageStack = new DockerImageStack(app, `tenkAnalyzerAgent-DockerImageStack`, deploymentProps);
const agentCoreStack = new AgentCoreStack(app, `tenkAnalyzerAgent-AgentCoreStack`, {
  ...deploymentProps,
  imageUri: dockerImageStack.imageUri
});
agentCoreStack.addDependency(dockerImageStack);
