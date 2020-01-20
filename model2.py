import numpy as np
import torch
import torch.nn as nn
from preprocessing import tokenizer
from transformers import XLMTokenizer, XLMWithLMHeadModel, XLMModel
from utilities import model_utils

device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
batch_size = 32
dic = tokenizer.decoder

class xlmb2b(nn.Module, model_utils):
    def __init__(self, dic = dic, d_model=1024, trfrmr_nlayers=4, pll_dat=True) :
        super().__init__()
        self.xlm = XLMModel.from_pretrained('xlm-mlm-ende-1024')
        self.d_model = d_model
        decoder_layer = torch.nn.TransformerDecoderLayer(self.d_model, nhead=8)
        self.trnsfrmr_dcodr = torch.nn.TransformerDecoder(decoder_layer, num_layers=trfrmr_nlayers)
        self.pll_data = pll_dat
        self.max_tr_seq_len = 120
        self.end_tok = 1 #Token id of end token
        self.dic_tensor = torch.tensor([v for k,v in tokenizer.encoder.items()],device=device) #tensor with i_th token's id at i_th position
        self.vocab_size = self.dic_tensor.shape[0]
        self.final_linear = nn.Linear(self.d_model, self.vocab_size)
        self.it_no = None
        self.beam_size = 1
        self.k = 1
        self.m = 1
        self.begin_prgrsiv_xlm_to_plt = False
        self.begin_prgrsiv_real_to_pred = False
    
    def choose(self) :
        '''Chooses final output beam for each sample using beam_size,
           final_out,prev_probs'''
        x = self.prev_probs.max(1, keepdim=True)[1]                       #batch_sizeX1Xbeam_size
        s = torch.gather(self.prev_probs, dim=1, index=x)      #batch_sizeX1Xbeam_size
        y = s.max(2)[1]                                                   #batch_sizeX1
        i = torch.tensor([i for i in range(y.shape[0])],device=device)
        final_out = torch.stack(self.final_out).transpose(0,1)
        final_out = final_out.reshape(self.beam_size,-1,final_out.shape[1])
        return self.prev_probs[i,:,y.reshape(-1)], self.sr_tokens, final_out[y.reshape(-1),i.reshape(-1),:]
    

    def get_prgrsiv_embdngs(self, dic, xlm_encoding) :
        plt_embdng = self.plt_embed(dic['input_ids'],dic['langs'], dic['position_ids'])
        return self.m*xlm_encoding+(1-self.m)*plt_embdng

    def get_prgrsiv_tr_embd(self, probs, prgrsiv_sr_embd, tr_embd, tr_dic) :
        '''Converts tr_embd->k*tr_embd+(1-k)*plt_embdng_of_pred
            using probs'''
        if not self.begin_prgrsiv_real_to_pred :
            return probs, prgrsiv_sr_embd, tr_embd
        tokens = probs.max(2)[1]
        plt_embdng = self.plt_embed(tokens, tr_dic['langs'], tr_dic['position_ids'])
        return probs, prgrsiv_sr_embd, self.m*(tr_embd)+(1-self.m)*plt_embdng
    
    def forward(self, dat, already_embed = False) :                             #dat is a dictionary with keys==keyword args of xlm

        if self.pll_data :
            inp = dat['X']
            out = dat['Y']

            if not already_embed :
                sr_embd =  self.xlm(**self.change_attn_for_xlm(inp))[0]
                tr_embd = self.xlm(**self.change_attn_for_xlm(out))[0]                                    #(xlm_out/trnsfrmr_tar).shape = (batch_size,seq_len,1024)
                prgrsiv_sr_embd = self.get_prgrsiv_embdngs(inp, sr_embd)
                prgrsiv_tr_embd = self.get_prgrsiv_embdngs(out, tr_embd)
            else :
                sr_embd = inp['input_ids']
                prgrsiv_tr_embd = out['input_ids']

            tr_len = int(out['lengths'].max())
            tgt_mask = self.get_tgt_mask(tr_len)
            trfrmr_out = self.trnsfrmr_dcodr(tgt=prgrsiv_tr_embd.transpose(0,1),
                                             memory=sr_embd.transpose(0,1), tgt_mask=tgt_mask,
                                             tgt_key_padding_mask=~(out['attention_mask'].bool()),
                                             memory_key_padding_mask=~(inp['attention_mask'].bool())).transpose(0,1)
            probs = self.apply_final_layer(trfrmr_out, out['attention_mask'].float())
            out['attention_mask'] = out['attention_mask'].float()
            if not already_embed :
                return self.get_prgrsiv_tr_embd(probs, prgrsiv_sr_embd, tr_embd, out)
            else :
                return probs, sr_embd, prgrsiv_tr_embd    
        else :

            inp = dat['X']
            self.sr_tokens = inp['input_ids']
            self.sr_embd = self.xlm(**self.change_attn_for_xlm(inp))[0].repeat_interleave(self.beam_size,0)
            self.bs = inp['input_ids'].shape[0]*self.beam_size
            self.tgt_key_pad_mask = torch.zeros((self.bs, self.max_tr_seq_len),device=device)
            self.mem_key_pad_mask = inp['attention_mask'].repeat_interleave(self.beam_size,0)
            self.tgt_mask = self.get_tgt_mask(self.max_tr_seq_len,0)
            self.tr_embd = torch.zeros((self.bs, self.max_tr_seq_len, self.d_model),dtype=torch.float64, device=device)
            self.not_done_samples = torch.ones(self.bs, dtype=torch.bool, device=device)
            self.it_no = 0                                                           #if nth word of target sequence is being predicted,
            self.final_out = []                                                      #then iteration number(it_no) == n-1
            self.lengs = torch.zeros((self.bs),device=device)
            self.actual_bs = int(self.bs/self.beam_size)
            self.prev_probs = torch.zeros((self.actual_bs,self.max_tr_seq_len+1,self.beam_size), device=device)
            self.tgt_key_pad_mask[:,self.it_no] = torch.ones((self.bs), device=device)
            self.just_now_completed_samples_mask = torch.zeros((self.bs), dtype=torch.bool, device=device)
            self.seq_len_sr = inp['lengths'].max()

            while True :

                trfrmr_out = self.trnsfrmr_dcodr(tgt=self.tr_embd.transpose(0,1),
                                                 memory=self.sr_embd.transpose(0,1), tgt_mask=tgt_mask,
                                                 tgt_key_padding_mask=~(self.tgt_key_pad_mask.bool()),
                                                 memory_key_padding_mask=~(self.mem_key_pad_mask.bool())).transpose(0,1)
                
                val, masky = self.apply_final_layer( trfrmr_out, self.tgt_key_pad_mask.float() )
                trfrmr_out = torch.zeros((self.bs,self.vocab_size), device=device)
                trfrmr_out[masky[:,self.it_no+1].bool()] = val
                self.tgt_key_pad_mask = self.tgt_key_pad_mask.long()
                dic_indices = self.reform(trfrmr_out)
                dic_indices[~self.not_done_samples] = tokenizer.pad_token_id
                output_at_it_no = torch.zeros((self.bs,1), dtype=torch.float64, device=device)
                output_at_it_no[self.not_done_samples] = self.dic_tensor[dic_indices].reshape(-1,1)[self.not_done_samples]
                self.final_out.append(output_at_it_no)
                self.tr_embd[self.not_done_samples,self.it_no+1,:] = self.embed_for_decoder(output_at_it_no[self.not_done_samples], inp['langs'][:,self.it_no])           #Adding next words embeddings to context for decoder
                
                ind = output_at_it_no[self.not_done_samples]!=self.end_tok
                ind=ind.reshape(-1)
                new_done_samples_len = (self.not_done_samples==True).sum()-(ind==True).sum()
                
                if new_done_samples_len!=0 :
                    self.calc_just_now_completed_samples_mask(ind)
                    self.lengs[self.just_now_completed_samples_mask] = it_no+1
                    self.mem_key_pad_mask[self.just_now_completed_samples_mask] = 0 #torch.zeros((new_done_samples_len, self.seq_len_sr)).long()
                    self.tgt_key_pad_mask[self.just_now_completed_samples_mask] = 0 #torch.zeros((new_done_samples_len, self.max_tr_seq_len)).long()
                
                self.tgt_key_pad_mask[self.mask_fr_mask()] = 1
                
                if self.not_done_samples.sum()==0 or self.it_no==self.max_tr_seq_len-1:
                    self.it_no = None
                    return self.choose()
                
                self.it_no+=1
                self.tgt_mask = self.get_tgt_mask(self.tgt_mask, self.it_no)