variable "name" {}
locals {
  suffix = "logs"
}
resource "aws_s3_bucket" "logs" {
  bucket = "${var.name}-${local.suffix}"
}
resource "aws_s3_bucket" "orphan" {
  bucket = "orphan"
}
resource "aws_s3_bucket_policy" "p" {
  bucket = aws_s3_bucket.logs.id
  policy = data.aws_iam_policy_document.doc.json
}
data "aws_iam_policy_document" "doc" {}
module "net" {
  source = "./net"
}
output "vpc" {
  value = module.net.vpc_id
}
